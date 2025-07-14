#!/usr/bin/env python3
"""
Screen Share Server GUI - A dedicated GUI application for the Python Screen Share Server
A clean, dedicated server application with graphical interface
"""

import os
import sys
import time
import socket
import signal
import random
import logging
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, BooleanVar, scrolledtext
from typing import Dict, Any, Optional, Callable, Tuple, List
import numpy as np
import cv2
from PIL import ImageGrab, Image
import struct
import pyaudio

# Default settings
TCP_PORT = 8000
UDP_PORT = 54545
AUDIO_ENABLED = True  # Default to audio enabled

# Audio settings
CHUNK_SIZE = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
AUDIO_FORMAT = pyaudio.paInt16  # Define the audio format constant

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)

# Global send lock to prevent concurrent socket writes from different threads
send_lock = threading.Lock()

# --- UDP Discovery Responder -------------------------------------------------
class UdpResponder:
    """UDP responder for LAN discovery"""
    def __init__(self, conn_id: str, password: str, tcp_port: int, udp_port: int = UDP_PORT):
        self.conn_id = conn_id
        self.password = password
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.running = False
        self.sock = None
    
    def run(self):
        """Run the UDP responder"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock.bind(('', self.udp_port))
            self.running = True
            
            logging.info(f"UDP responder listening on port {self.udp_port}")
            
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(1024)
                    message = data.decode('utf-8')
                    
                    if message.startswith('DISCOVER_SCREEN_SHARE'):
                        logging.info(f"Discovery request from {addr[0]}:{addr[1]}")
                        response = f"SCREEN_SHARE_INFO:{self.conn_id}:{self.password}:{self.tcp_port}"
                        self.sock.sendto(response.encode('utf-8'), addr)
                except Exception as e:
                    if self.running:  # Only log if still running (not stopped)
                        logging.error(f"UDP responder error: {e}")
                        time.sleep(1)
        except Exception as e:
            logging.error(f"Failed to start UDP responder: {e}")
        finally:
            if self.sock:
                self.sock.close()
                
    def stop(self):
        """Stop the UDP responder"""
        self.running = False
        if self.sock:
            try:
                # Send a message to self to unblock recvfrom
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(b'STOP', ('127.0.0.1', self.udp_port))
                sock.close()
            except:
                pass

# --- Screen Share Server -----------------------------------------------------
class ScreenShareServer:
    """Screen sharing server implementation"""
    def __init__(self, port: int = TCP_PORT, status_callback: Optional[Callable] = None):
        self.port = port
        self.running = False
        self.server_socket = None
        self.clients = []
        self.client_addresses = {}
        self.client_count = 0
        self.frame_count = 0
        self.bytes_sent = 0
        self.start_time = time.time()
        self.send_lock = threading.Lock()  # Lock for thread-safe socket sending
        self.status_callback = status_callback
        self.screen_thread = None
        self.audio_thread = None
    
    def start(self) -> bool:
        """Start the screen share server"""
        try:
            # Create server socket
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(('', self.port))
            self.server_socket.listen(5)
            self.running = True
            
            # Start threads
            self.screen_thread = threading.Thread(target=self._screen_capture_thread)
            self.screen_thread.daemon = True
            self.screen_thread.start()
            
            # Start audio thread if enabled
            if AUDIO_ENABLED:
                self.audio_thread = threading.Thread(target=self._audio_capture_thread)
                self.audio_thread.daemon = True
                self.audio_thread.start()
            
            # Start client acceptance thread
            threading.Thread(target=self._accept_clients, daemon=True).start()
            
            logging.info(f"Screen share server started on port {self.port}")
            return True
        except Exception as e:
            logging.error(f"Failed to start server: {e}")
            return False
    
    def stop(self):
        """Stop the screen share server"""
        self.running = False
        
        # Close all client connections
        for client in self.clients:
            try:
                client.close()
            except:
                pass
        
        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        
        self.clients = []
        self.client_addresses = {}
        logging.info("Server stopped")
    
    def _accept_clients(self):
        """Accept client connections"""
        while self.running:
            try:
                client, addr = self.server_socket.accept()
                logging.info(f"Client connected: {addr[0]}:{addr[1]}")
                
                # Add client to list
                self.clients.append(client)
                self.client_addresses[client] = f"{addr[0]}:{addr[1]}"
                self.client_count += 1
                
                # Notify callback if available
                if self.status_callback:
                    self.status_callback("client_connected", f"{addr[0]}:{addr[1]}")
                
                # Start a thread to monitor this client
                threading.Thread(target=self._client_monitor, args=(client, addr), daemon=True).start()
            except Exception as e:
                if self.running:  # Only log if not intentionally stopped
                    logging.error(f"Error accepting client: {e}")
                time.sleep(0.5)
    
    def _client_monitor(self, client, addr):
        """Monitor a client for disconnection"""
        try:
            # Simple ping mechanism - client disconnects when it can't receive
            while self.running:
                try:
                    client.settimeout(2.0)
                    data = client.recv(1024)
                    if not data:
                        break
                except:
                    # Check if client is still connected
                    try:
                        # Try sending a small packet
                        client.send(b'PING')
                    except:
                        break
                time.sleep(2.0)
        except:
            pass
        finally:
            if client in self.clients:
                self.clients.remove(client)
                client_addr = self.client_addresses.pop(client, f"{addr[0]}:{addr[1]}")
                self.client_count = max(0, self.client_count - 1)
                logging.info(f"Client disconnected: {client_addr}")
                
                # Notify callback if available
                if self.status_callback:
                    self.status_callback("client_disconnected", client_addr)
    
    def _screen_capture_thread(self):
        """Capture and send screen frames"""
        try:
            import cv2
            import pyautogui
            from PIL import ImageGrab
        except ImportError as e:
            logging.error(f"Missing required modules: {e}")
            return
        
        last_frame_time = time.time()
        fps_target = 30  # Target frames per second
        frame_delay = 1.0 / fps_target
        
        while self.running and self.clients:
            try:
                # Capture screen
                screen = ImageGrab.grab()
                frame = np.array(screen)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                
                # Send to clients
                if self.clients:  # Double check we have clients
                    # Convert to JPEG
                    _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    frame_data = buffer.tobytes()
                    header = f"VIDEO:{len(frame_data)}:\n".encode('utf-8')
                    
                    # Track stats
                    self.frame_count += 1
                    self.bytes_sent += len(header) + len(frame_data)
                    
                    # Send to all clients with thread safety
                    with self.send_lock:  # Use lock to prevent message interleaving
                        for client in self.clients.copy():  # Use copy to avoid modification during iteration
                            try:
                                client.sendall(header + frame_data)
                            except:
                                # Client will be removed by monitor thread
                                pass
                
                # Control frame rate
                current_time = time.time()
                elapsed = current_time - last_frame_time
                sleep_time = frame_delay - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
                last_frame_time = time.time()
                
            except Exception as e:
                logging.error(f"Screen capture error: {e}")
                time.sleep(1.0)
            
            # Sleep if no clients
            if not self.clients:
                time.sleep(0.5)
    
    def _audio_capture_thread(self):
        """Capture and send audio"""
        if not AUDIO_ENABLED:
            return
            
        try:
            import pyaudio
        except ImportError as e:
            logging.error(f"Missing PyAudio module: {e}")
            return
        
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        
        try:
            p = pyaudio.PyAudio()
            stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
            
            while self.running and self.clients:
                try:
                    audio_data = stream.read(CHUNK, exception_on_overflow=False)
                    
                    if self.clients:  # Check we have clients
                        # Calculate data size and create header
                        data_size = len(audio_data)
                        header = f"AUDIO:{data_size}:\n".encode('utf-8')
                        
                        # Track stats
                        self.bytes_sent += len(header) + data_size
                        
                        # Send to all clients with thread safety
                        with self.send_lock:  # Use lock to prevent message interleaving
                            for client in self.clients.copy():  # Use copy to avoid modification during iteration
                                try:
                                    client.sendall(header + audio_data)
                                except:
                                    # Client will be removed by monitor thread
                                    pass
                    
                    # Control CPU usage if no clients
                    if not self.clients:
                        time.sleep(0.1)
                        
                except Exception as e:
                    logging.error(f"Audio capture error: {e}")
                    time.sleep(0.5)
        except Exception as e:
            logging.error(f"Failed to initialize audio: {e}")
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except:
                pass
                
        self.root.title("Screen Share Server")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Server configuration variables
        self.conn_id = tk.StringVar(value=f"{random.randint(100000, 999999)}")
        self.password = tk.StringVar(value=f"{random.randint(1000, 9999)}")
        self.tcp_port = tk.IntVar(value=TCP_PORT)
        self.udp_port = tk.IntVar(value=UDP_PORT)
        self.audio_enabled = BooleanVar(value=AUDIO_ENABLED)
        
        # Server components
        self.udp_responder = None
        self.server = None
        self.server_running = False
        
        # Status variables
        self.connected_clients = []  # List to track connected clients
        self.status_text = None
        self.log_text = None
        self.client_listbox = None
        
        # Create the GUI components
        self.create_gui()
        self.setup_logging()
        
        # Setup status update timer
        self.root.after(1000, self.update_stats)
    
    def create_gui(self):
        """Create the GUI components"""
        # Create main frame with padding
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Configuration section
        config_frame = ttk.LabelFrame(main_frame, text="Server Configuration", padding="10")
        config_frame.pack(fill=tk.X, pady=5)
        
        # Grid for config inputs
        ttk.Label(config_frame, text="Connection ID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(config_frame, textvariable=self.conn_id, width=15).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(config_frame, text="Password:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(config_frame, textvariable=self.password, width=10).grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(config_frame, text="TCP Port:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(config_frame, textvariable=self.tcp_port, width=10).grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(config_frame, text="UDP Port:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(config_frame, textvariable=self.udp_port, width=10).grid(row=1, column=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Checkbutton(config_frame, text="Enable Audio", variable=self.audio_enabled).grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)
        
        # Server control buttons
        control_frame = ttk.Frame(main_frame, padding=5)
        control_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(control_frame, text="Start Server", command=self.start_server).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Stop Server", command=self.stop_server).pack(side=tk.LEFT, padx=5)
        
        # Server status display
        status_frame = ttk.LabelFrame(main_frame, text="Server Status", padding="10")
        status_frame.pack(fill=tk.X, pady=5)
        
        self.status_text = ttk.Label(status_frame, text="Server stopped")
        self.status_text.pack(fill=tk.X)
        
        # Split frame for logs and client list
        split_frame = ttk.Frame(main_frame)
        split_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Client list on the left
        client_frame = ttk.LabelFrame(split_frame, text="Connected Clients", padding="5")
        client_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        self.client_listbox = tk.Listbox(client_frame)
        self.client_listbox.pack(fill=tk.BOTH, expand=True)
        
        # Log display on the right
        log_frame = ttk.LabelFrame(split_frame, text="Server Logs", padding="5")
        log_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True)
    
    def setup_logging(self):
        """Set up logging to redirect to the GUI"""
        class TextHandler(logging.Handler):
            def __init__(self, text_widget):
                logging.Handler.__init__(self)
                self.text_widget = text_widget
            
            def emit(self, record):
                msg = self.format(record)
                self.text_widget.configure(state='normal')
                self.text_widget.insert(tk.END, msg + '\n')
                self.text_widget.see(tk.END)
                self.text_widget.configure(state='disabled')
        
        # Add the text handler to the root logger
        handler = TextHandler(self.log_text)
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
        handler.setFormatter(formatter)
        
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        
        # Set initial state
        self.log_text.configure(state='disabled')
        logging.info("Logging initialized in GUI")
        
    def start_server(self):
        """Start the server and UDP responder"""
        if not self.server_running:
            try:
                # Start the screen share server
                self.server = ScreenShareServer(
                    port=self.tcp_port.get(),
                    status_callback=self.server_status_callback,
                    audio_enabled=self.audio_enabled.get()
                )
                if self.server.start():
                    # Start the UDP responder for discovery
                    self.start_udp_responder()
                    
                    self.server_running = True
                    self.status_text.config(text="Server running")
                    logging.info(f"Server started on port {self.tcp_port.get()}")
                    logging.info(f"Connection ID: {self.conn_id.get()}, Password: {self.password.get()}")
                else:
                    logging.error("Failed to start server")
            except Exception as e:
                logging.error(f"Error starting server: {e}")
            
    def stop_server(self):
        """Stop the server and UDP responder"""
        if self.server_running:
            # Stop the UDP responder
            self.stop_udp_responder()
            
            # Stop the screen share server
            self.server.stop()
            self.server = None
            self.server_running = False
            self.status_text.config(text="Server stopped")
            logging.info("Server stopped")
            
            # Clear the client list
            self.connected_clients = []
            self.update_client_list()
    
    def server_status_callback(self, event_type, data):
        """Callback for server status updates"""
        if event_type == "client_connected":
            client_addr = data[0]
            self.connected_clients.append(client_addr)
            self.update_client_list()
            logging.info(f"Client connected: {client_addr}")
        elif event_type == "client_disconnected":
            client_addr = data[0]
            if client_addr in self.connected_clients:
                self.connected_clients.remove(client_addr)
                self.update_client_list()
                logging.info(f"Client disconnected: {client_addr}")
        elif event_type == "error":
            logging.error(f"Server error: {data}")
    
    def update_client_list(self):
        """Update the client list display"""
        self.client_listbox.delete(0, tk.END)
        for client in self.connected_clients:
            self.client_listbox.insert(tk.END, client)
    
    def update_stats(self):
        """Update server statistics"""
        if self.server_running and self.server:
            try:
                # Get stats from server
                stats = self.server.get_stats()
                
                # Format stats for display
                fps = f"{stats['fps']:.2f}"
                mbps = f"{stats['mbps']:.2f}"
                clients = stats['clients']
                runtime = self.format_time(stats['runtime'])
                
                # Get local IP
                local_ip = socket.gethostbyname(socket.gethostname())
                
                # Update status text
                status_str = f"Status: Running | FPS: {fps} | Bandwidth: {mbps} MB/s | "
                status_str += f"Clients: {clients} | Runtime: {runtime} | IP: {local_ip}"
                self.status_text.config(text=status_str)
            except Exception as e:
                logging.error(f"Error updating stats: {e}")
        
        # Schedule the next update
        self.root.after(1000, self.update_stats)
    
    def format_time(self, seconds):
        """Format time in seconds to HH:MM:SS"""
        hours, remainder = divmod(int(seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def on_close(self):
        """Handle window close event"""
        if self.server_running:
            self.stop_server()
        self.root.destroy()
            
    def start_udp_responder(self):
        """Start the UDP responder"""
        if not self.udp_responder:
            self.udp_responder = UdpResponder(
                port=self.udp_port.get(), 
                connection_id=self.conn_id.get(), 
                password=self.password.get(),
                tcp_port=self.tcp_port.get()
            )
            self.udp_responder.run()
            
    def stop_udp_responder(self):
        """Stop the UDP responder"""
        if self.udp_responder:
            self.udp_responder.stop()
            self.udp_responder = None
            
class UdpResponder:
    def __init__(self, port, connection_id: str, password: str, tcp_port = TCP_PORT):
        # Ensure port is an integer and within valid range
        try:
            port_int = int(port)
            if port_int < 0 or port_int > 65535:
                logging.error(f"Invalid port number: {port_int}. Using default UDP_PORT: {UDP_PORT}")
                port_int = UDP_PORT
            self.port = port_int
        except ValueError:
            logging.error(f"Could not convert port to integer: {port}. Using default UDP_PORT: {UDP_PORT}")
            self.port = UDP_PORT
        
        # Validate TCP port as well
        try:
            tcp_port_int = int(tcp_port)
            if tcp_port_int < 0 or tcp_port_int > 65535:
                logging.error(f"Invalid TCP port: {tcp_port_int}. Using default TCP_PORT: {TCP_PORT}")
                tcp_port_int = TCP_PORT
            self.tcp_port = tcp_port_int
        except ValueError:
            logging.error(f"Could not convert TCP port to integer: {tcp_port}. Using default TCP_PORT: {TCP_PORT}")
            self.tcp_port = TCP_PORT
            
        self.connection_id = connection_id
        self.password = password
        self.running = threading.Event()
        self.running.set()
        self.sock = None
        
    def run(self):
        """Run the UDP responder to handle discovery requests."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        
        logging.info(f"UDP responder started on port {self.port}")
        
        while self.running.is_set():
            try:
                self.sock.settimeout(1.0)
                data, addr = self.sock.recvfrom(1024)
                message = data.decode("utf-8").strip()
                
                # Verify connection ID and password
                parts = message.split(":")
                if len(parts) == 3 and parts[0] == "DISCOVER" and parts[1] == self.connection_id and parts[2] == self.password:
                    # Get local IP address
                    local_ip = socket.gethostbyname(socket.gethostname())
                    # Send back the server IP and port
                    response = f"SERVER:{local_ip}:{self.tcp_port}"
                    self.sock.sendto(response.encode("utf-8"), addr)
                    logging.info(f"Responded to discovery request from {addr}")
            except socket.timeout:
                continue
            except Exception as e:
                logging.error(f"Error in UDP responder: {e}")
                break
                
    def stop(self):
        """Stop the UDP responder."""
        self.running.clear()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


class ScreenShareServer:
    def __init__(self, port: int = TCP_PORT, status_callback=None, audio_enabled=True):
        self.port = port
        self.clients: Dict[socket.socket, Tuple[str, int]] = {}
        self.lock = threading.Lock()
        self.running = threading.Event()
        self.running.set()
        self.server_socket = None
        self.screen_thread = None
        self.audio_thread = None
        self.audio = None
        self.screenshot_timestamp = 0
        self.status_callback = status_callback
        self.audio_enabled = audio_enabled
        self.frame_count = 0
        self.client_count = 0
        self.bytes_sent = 0
        self.start_time = time.time()
        
    def initialize_audio(self):
        """Initialize PyAudio stream"""
        try:
            self.audio = pyaudio.PyAudio()
            logging.info("Audio capture initialized")
            return True
        except Exception as e:
            logging.error(f"Failed to initialize audio: {e}")
            return False
            
    def start(self):
        """Start the server and all threads"""
        self.start_time = time.time()
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            self.server_socket.bind(("0.0.0.0", self.port))
            self.server_socket.listen(5)
            logging.info(f"Server started on 0.0.0.0:{self.port}")
            
            if self.audio_enabled:
                if self.initialize_audio():
                    logging.info("Audio support: Enabled")
                else:
                    logging.warning("Audio support: Disabled due to initialization error")
            else:
                logging.info("Audio support: Disabled")
            
            # Start threads
            self.screen_thread = threading.Thread(target=self.screen_capture_thread)
            self.screen_thread.daemon = True
            self.screen_thread.start()
            
            if self.audio_enabled and self.audio:
                self.audio_thread = threading.Thread(target=self.audio_capture_thread)
                self.audio_thread.daemon = True
                self.audio_thread.start()
            
            # Accept client connections
            accept_thread = threading.Thread(target=self.accept_clients)
            accept_thread.daemon = True
            accept_thread.start()
            
            return True
        except Exception as e:
            logging.error(f"Failed to start server: {e}")
            return False
        
    def stop(self):
        """Stop the server and all threads"""
        self.running.clear()
        
        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        
        # Close client connections
        with self.lock:
            for client in list(self.clients.keys()):
                try:
                    client.close()
                except:
                    pass
            self.clients.clear()
        
        # Clean up audio if used
        if self.audio:
            try:
                self.audio.terminate()
                self.audio = None
            except:
                pass
            
        logging.info("Server stopped")
        
    def accept_clients(self):
        """Accept client connections"""
        self.server_socket.settimeout(1.0)
        while self.running.is_set():
            try:
                client, addr = self.server_socket.accept()
                # Start a thread to handle client communication
                client_thread = threading.Thread(target=self.client_monitor, args=(client, addr))
                client_thread.daemon = True
                client_thread.start()
                
                # Update client count
                with self.lock:
                    self.clients[client] = addr
                    self.client_count = len(self.clients)
                    
                # Send connection acknowledgment
                try:
                    with send_lock:
                        client.sendall(b"CONNECTED")
                except:
                    pass
                    
                # Notify via callback if provided
                if self.status_callback:
                    self.status_callback("client_connected", addr)
                    
                logging.info(f"Client connected from {addr[0]}:{addr[1]}")
            except socket.timeout:
                continue
            except Exception as e:
                if self.running.is_set():
                    logging.error(f"Error accepting client connection: {e}")
                    
    def client_monitor(self, client, addr):
        """Monitor client connection and handle commands"""
        while self.running.is_set():
            try:
                client.settimeout(1.0)
                data = client.recv(1024)
                if not data:
                    break
                    
                # Process client commands here if needed
                command = data.decode("utf-8").strip()
                if command == "PING":
                    with send_lock:
                        client.sendall(b"PONG")
                        
            except socket.timeout:
                continue
            except Exception as e:
                if self.running.is_set():
                    logging.error(f"Error in client communication: {e}")
                break
                
        # Client disconnected
        with self.lock:
            if client in self.clients:
                del self.clients[client]
                self.client_count = len(self.clients)
                
        try:
            client.close()
        except:
            pass
            
        # Notify via callback if provided
        if self.status_callback:
            self.status_callback("client_disconnected", addr)
            
        logging.info(f"Client disconnected from {addr[0]}:{addr[1]}")
        
    def screen_capture_thread(self):
        """Capture screen frames and send to clients"""
        # Setup for FPS control
        target_fps = 30
        frame_interval = 1.0 / target_fps
        next_frame_time = time.time()
        
        logging.info("Screen capture thread started")
        
        while self.running.is_set():
            try:
                current_time = time.time()
                
                # Sleep to control FPS
                if current_time < next_frame_time:
                    time.sleep(next_frame_time - current_time)
                    
                # Calculate next frame time
                next_frame_time = max(current_time, next_frame_time) + frame_interval
                
                # Skip frame capture if no clients
                if not self.clients:
                    time.sleep(0.1)
                    continue
                    
                # Capture screen
                screenshot = ImageGrab.grab()
                frame = np.array(screenshot)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Compress frame as JPEG
                _, jpeg_frame = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                jpeg_bytes = jpeg_frame.tobytes()
                frame_size = len(jpeg_bytes)
                
                # Prepare header: VIDEO:<size>:
                header = f"VIDEO:{frame_size}:".encode("utf-8")
                frame_data = header + jpeg_bytes
                
                # Send to all clients
                disconnected_clients = []
                with self.lock:
                    for client in self.clients:
                        try:
                            with send_lock:
                                client.sendall(frame_data)
                            self.bytes_sent += len(frame_data)
                        except:
                            disconnected_clients.append(client)
                            
                # Clean up disconnected clients
                if disconnected_clients:
                    with self.lock:
                        for client in disconnected_clients:
                            if client in self.clients:
                                addr = self.clients[client]
                                del self.clients[client]
                                if self.status_callback:
                                    self.status_callback("client_disconnected", addr)
                    self.client_count = len(self.clients)
                
                # Update frame count
                self.frame_count += 1
                
            except Exception as e:
                logging.error(f"Error in screen capture: {e}")
                time.sleep(1.0)  # Avoid tight error loop
                
        logging.info("Screen capture thread stopped")
        
    def audio_capture_thread(self):
        """Capture audio and send to clients"""
        if not self.audio:
            return
            
        try:
            # Audio parameters
            CHUNK = 1024
            FORMAT = pyaudio.paInt16
            CHANNELS = 1
            RATE = 44100
            
            # Open audio stream
            stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            
            logging.info("Audio capture thread started")
            
            while self.running.is_set():
                try:
                    # Skip audio capture if no clients
                    if not self.clients:
                        time.sleep(0.1)
                        continue
                        
                    # Read audio chunk
                    audio_data = stream.read(CHUNK, exception_on_overflow=False)
                    
                    # Prepare header: AUDIO:<size>:
                    header = f"AUDIO:{len(audio_data)}:".encode("utf-8")
                    audio_packet = header + audio_data
                    
                    # Send to all clients
                    disconnected_clients = []
                    with self.lock:
                        for client in self.clients:
                            try:
                                with send_lock:
                                    client.sendall(audio_packet)
                                self.bytes_sent += len(audio_packet)
                            except:
                                disconnected_clients.append(client)
                                
                    # Clean up disconnected clients
                    if disconnected_clients:
                        with self.lock:
                            for client in disconnected_clients:
                                if client in self.clients:
                                    addr = self.clients[client]
                                    del self.clients[client]
                                    if self.status_callback:
                                        self.status_callback("client_disconnected", addr)
                        self.client_count = len(self.clients)
                        
                except Exception as e:
                    if self.running.is_set():  # Only log if still running
                        logging.error(f"Error in audio capture: {e}")
                    break
                    
            # Clean up
            stream.stop_stream()
            stream.close()
            
        except Exception as e:
            logging.error(f"Error in audio thread: {e}")
            
        logging.info("Audio capture thread stopped")
                
        logging.info("Server stopped")
        
    def accept_clients(self):
        """Accept new client connections"""
        while self.running.is_set():
            try:
                self.server_socket.settimeout(1.0)
                client, addr = self.server_socket.accept()
                with self.lock:
                    self.clients[client] = addr
                    self.client_count = len(self.clients)
                    
                logging.info(f"New connection from {addr}")
                if self.status_callback:
                    self.status_callback("client_connected", addr)
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running.is_set():
                    logging.error(f"Error accepting client: {e}")
                break
    
    def remove_client(self, client_socket):
        """Remove a client from the clients dictionary"""
        with self.lock:
            if client_socket in self.clients:
                addr = self.clients[client_socket]
                del self.clients[client_socket]
                self.client_count = len(self.clients)
                logging.info(f"Connection from {addr} closed")
                if self.status_callback:
                    self.status_callback("client_disconnected", addr)
                
    def send_to_clients(self, message_type: str, data: bytes):
        """Send data to all connected clients with thread safety"""
        header = f"{message_type}:{len(data)}:".encode("utf-8")
        
        with send_lock:  # Use global send lock to prevent concurrent writes
            with self.lock:
                clients_copy = list(self.clients.keys())
                
            for client in clients_copy:
                try:
                    client.sendall(header + data)
                    self.bytes_sent += len(header) + len(data)
                except Exception as e:
                    logging.error(f"Error sending to client: {e}")
                    try:
                        client.close()
                    except:
                        pass
                    self.remove_client(client)
                
    def screen_capture_thread(self):
        """Capture and send screen frames to all clients"""
        # Constants for screen capture
        FRAME_RATE = 30  # Target frames per second
        frame_interval = 1.0 / FRAME_RATE
        next_frame_time = time.time()
        
        logging.info("Screen capture thread started")
        
        while self.running.is_set():
            try:
                current_time = time.time()
                
                # Sleep to control FPS
                if current_time < next_frame_time:
                    time.sleep(next_frame_time - current_time)
                    
                # Calculate next frame time
                next_frame_time = max(current_time, next_frame_time) + frame_interval
                
                # Skip frame capture if no clients
                if not self.clients:
                    time.sleep(0.1)
                    continue
                    
                # Capture screen
                screenshot = ImageGrab.grab()
                frame = np.array(screenshot)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Compress frame as JPEG
                _, jpeg_frame = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                jpeg_bytes = jpeg_frame.tobytes()
                
                # Send to all clients
                self.send_to_clients("VIDEO", jpeg_bytes)
                
                # Update frame count
                self.frame_count += 1
                
            except Exception as e:
                logging.error(f"Error in screen capture: {e}")
                time.sleep(1.0)  # Avoid tight error loop
                
        logging.info("Screen capture thread stopped")
    
    def audio_capture_thread(self):
        """Capture and send audio to all clients"""
        if not self.audio:
            return
            
        try:
            stream = self.audio.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE
            )
            
            while self.running.is_set():
                try:
                    audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    self.send_to_clients("AUD", audio_data)
                except Exception as e:
                    logging.error(f"Error in audio capture: {e}")
                    time.sleep(0.1)
                    
        except Exception as e:
            logging.error(f"Error setting up audio stream: {e}")
        finally:
            try:
                if 'stream' in locals() and stream:
                    stream.stop_stream()
                    stream.close()
            except:
                pass
                
    def get_stats(self):
        """Get server statistics"""
        runtime = time.time() - self.start_time
        if runtime > 0:
            fps = self.frame_count / runtime
            mbps = (self.bytes_sent / (1024 * 1024)) / runtime
        else:
            fps = 0
            mbps = 0
            
        return {
            "fps": fps,
            "mbps": mbps,
            "clients": self.client_count,
            "frames": self.frame_count,
            "runtime": runtime,
            "bytes_sent": self.bytes_sent
        }

# --- GUI Implementation -----------------------------------------------------
class ServerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Python Screen Share Server")
        self.root.geometry("600x500")
        self.root.minsize(500, 400)
        
        # Set app icon if available
        try:
            # Uncomment if you have an icon file
            # self.root.iconbitmap("server_icon.ico")
            pass
        except:
            pass
            
        # Variables
        self.conn_id = tk.StringVar(value="".join(random.choices("0123456789", k=6)))
        self.password = tk.StringVar(value="".join(random.choices("0123456789", k=4)))
        self.port = tk.IntVar(value=TCP_PORT)
        self.discovery_port = tk.IntVar(value=UDP_PORT)
        self.audio_enabled = BooleanVar(value=AUDIO_ENABLED)
        self.server = None
        self.udp_responder = None
        self.server_running = False
        self.stats_timer = None
        
        # Client tracking
        self.connected_clients = []
        
        # Create the UI
        self.create_gui()
        
        # Set up closing handler
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
    def create_gui(self):
        """Create the server GUI"""
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="20 20 20 20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Top section - Server settings
        settings_frame = ttk.LabelFrame(main_frame, text="Server Settings")
        settings_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Grid for settings
        ttk.Label(settings_frame, text="Connection ID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(settings_frame, textvariable=self.conn_id, width=10).grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="Password:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(settings_frame, textvariable=self.password, width=6).grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="TCP Port:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(settings_frame, textvariable=self.port, width=6).grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="UDP Port:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(settings_frame, textvariable=self.discovery_port, width=6).grid(row=1, column=3, padx=5, pady=5)
        
        ttk.Checkbutton(settings_frame, text="Enable Audio", variable=self.audio_enabled).grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)
        
        # Start/Stop buttons
        button_frame = ttk.Frame(settings_frame)
        button_frame.grid(row=2, column=2, columnspan=2, padx=5, pady=5)
        
        self.start_button = ttk.Button(button_frame, text="Start Server", command=self.start_server)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = ttk.Button(button_frame, text="Stop Server", command=self.stop_server, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Middle section - Status display
        status_frame = ttk.LabelFrame(main_frame, text="Server Status")
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Status indicators
        self.status_var = tk.StringVar(value="Server not running")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, font=(None, 10, "bold"))
        self.status_label.grid(row=0, column=0, columnspan=4, sticky=tk.W, padx=10, pady=5)
        
        # Create frames for statistics
        stats_frame = ttk.Frame(status_frame)
        stats_frame.grid(row=1, column=0, columnspan=4, sticky=tk.EW, padx=10, pady=5)
        
        # Statistics display
        ttk.Label(stats_frame, text="FPS:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.fps_var = tk.StringVar(value="--")
        ttk.Label(stats_frame, textvariable=self.fps_var).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        
        ttk.Label(stats_frame, text="Bandwidth:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.bandwidth_var = tk.StringVar(value="--")
        ttk.Label(stats_frame, textvariable=self.bandwidth_var).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        
        ttk.Label(stats_frame, text="Connected Clients:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.clients_var = tk.StringVar(value="0")
        ttk.Label(stats_frame, textvariable=self.clients_var).grid(row=0, column=3, sticky=tk.W, padx=5, pady=2)
        
        ttk.Label(stats_frame, text="Runtime:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)
        self.runtime_var = tk.StringVar(value="--")
        ttk.Label(stats_frame, textvariable=self.runtime_var).grid(row=1, column=3, sticky=tk.W, padx=5, pady=2)
        
        # Local IP address
        ip_frame = ttk.Frame(status_frame)
        ip_frame.grid(row=2, column=0, columnspan=4, sticky=tk.EW, padx=10, pady=5)
        
        ttk.Label(ip_frame, text="Local IP:").pack(side=tk.LEFT, padx=5)
        self.local_ip_var = tk.StringVar(value=socket.gethostbyname(socket.gethostname()))
        ttk.Entry(ip_frame, textvariable=self.local_ip_var, state="readonly", width=15).pack(side=tk.LEFT, padx=5)
        
        # Bottom section - Log display with scrollbar
        log_frame = ttk.LabelFrame(main_frame, text="Server Log")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        # Log area
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text.config(state=tk.DISABLED)
        
        # Footer with connected clients
        footer_frame = ttk.LabelFrame(main_frame, text="Connected Clients")
        footer_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.client_list_var = tk.StringVar(value="No clients connected")
        ttk.Label(footer_frame, textvariable=self.client_list_var).pack(padx=10, pady=5, anchor=tk.W)
        
        # Setup custom logging handler
        self.setup_logging()
    
    def setup_logging(self):
        """Set up custom logging handler to display in text widget"""
        class TextHandler(logging.Handler):
            def __init__(self, text_widget):
                logging.Handler.__init__(self)
                self.text_widget = text_widget
                
            def emit(self, record):
                msg = self.format(record)
                self.text_widget.config(state=tk.NORMAL)
                self.text_widget.insert(tk.END, msg + '\n')
                self.text_widget.see(tk.END)
                self.text_widget.config(state=tk.DISABLED)
        
        text_handler = TextHandler(self.log_text)
        text_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', '%H:%M:%S'))
        
        # Add handler to logger
        root_logger = logging.getLogger()
        root_logger.addHandler(text_handler)
    
    def start_server(self):
        """Start the screen share server"""
        if not self.server_running:
            global AUDIO_ENABLED
            AUDIO_ENABLED = self.audio_enabled.get()
            
            # Update status
            self.status_var.set("Starting server...")
            self.log_info(f"Starting server on port {self.port.get()}")
            
            # Start UDP responder
            self.udp_responder = UdpResponder(
                self.conn_id.get(),
                self.password.get(),
                self.port.get()
            )
            udp_thread = threading.Thread(target=self.udp_responder.run)
            udp_thread.daemon = True
            udp_thread.start()
            
            # Start screen share server
            self.server = ScreenShareServer(self.port.get(), self.server_status_callback)
            if not self.server.start():
                self.log_error("Failed to start server")
                self.status_var.set("Server failed to start")
                return
                
            # Update UI
            self.server_running = True
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            self.status_var.set("Server running")
            
            # Start stats update timer
            self.update_stats()
            
            # Log connection info
            self.log_info(f"Connection ID: {self.conn_id.get()}")
            self.log_info(f"Password: {self.password.get()}")
            self.log_info("Server started successfully")
    
    def stop_server(self):
        """Stop the screen share server"""
        if self.server_running:
            # Stop stats timer
            if self.stats_timer:
                self.root.after_cancel(self.stats_timer)
                self.stats_timer = None
            
            # Stop server
            if self.server:
                self.server.stop()
                self.server = None
                
            # Stop UDP responder
            if self.udp_responder:
                self.udp_responder.stop()
                self.udp_responder = None
                
            # Update UI
            self.server_running = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.status_var.set("Server stopped")
            
            # Clear stats
            self.fps_var.set("--")
            self.bandwidth_var.set("--")
            self.clients_var.set("0")
            self.runtime_var.set("--")
            
            # Clear client list
            self.connected_clients = []
            self.client_list_var.set("No clients connected")
            
            # Log
            self.log_info("Server stopped")
    
    def server_status_callback(self, event_type, data):
        """Callback for server events"""
        if event_type == "client_connected":
            self.connected_clients.append(str(data))
            self.update_client_list()
        elif event_type == "client_disconnected":
            if str(data) in self.connected_clients:
                self.connected_clients.remove(str(data))
            self.update_client_list()
    
    def update_client_list(self):
        """Update the client list display"""
        if not self.connected_clients:
            self.client_list_var.set("No clients connected")
        else:
            self.client_list_var.set(", ".join(self.connected_clients))
    
    def update_stats(self):
        """Update server statistics"""
        if self.server_running and self.server:
            stats = self.server.get_stats()
            
            # Format nicely
            self.fps_var.set(f"{stats['fps']:.1f}")
            self.bandwidth_var.set(f"{stats['mbps']:.2f} MB/s")
            self.clients_var.set(str(stats['clients']))
            
            # Format runtime as HH:MM:SS
            hours, remainder = divmod(int(stats['runtime']), 3600)
            minutes, seconds = divmod(remainder, 60)
            self.runtime_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            
            # Schedule next update
            self.stats_timer = self.root.after(1000, self.update_stats)
    
    def log_info(self, message):
        """Log an info message"""
        logging.info(message)
    
    def log_error(self, message):
        """Log an error message"""
        logging.error(message)
    
    def on_close(self):
        """Handle window closing"""
        if self.server_running:
            self.stop_server()
        self.root.destroy()

if __name__ == "__main__":
    # Set up signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        if 'gui_app' in globals():
            gui_app.on_close()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start the application
    root = tk.Tk()
    root.configure(bg='white')  # Set background color
    
    # Apply a theme if ttk is available
    style = ttk.Style()
    try:
        style.theme_use('clam')  # try to use a nicer theme
    except:
        pass  # fallback to default
    
    # Create and start the GUI
    global gui_app
    gui_app = ServerGUI(root)
    root.mainloop()
