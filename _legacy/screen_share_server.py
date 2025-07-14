#!/usr/bin/env python3
"""
Headless Screen Share Server - Docker-friendly version
"""
import os
import sys
import time
import threading
import socket
import logging
import random
import signal
import cv2
import numpy as np
import pyautogui
from PIL import Image
import pyaudio
import struct
import argparse
from typing import Dict, Tuple, Optional, List, Set

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Constants
TCP_PORT = int(os.environ.get("PORT", 8000))
UDP_PORT = int(os.environ.get("DISCOVERY_PORT", 54545))
CONNECTION_ID = os.environ.get("CONNECTION_ID", "".join(random.choices("0123456789", k=6)))
PASSWORD = os.environ.get("PASSWORD", "".join(random.choices("0123456789", k=4)))
FRAME_RATE = 30
CHUNK_SIZE = 1024
AUDIO_FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
AUDIO_ENABLED = True  # Can be modified via command line

# Global send lock to prevent concurrent socket writes from different threads
send_lock = threading.Lock()

class UdpResponder:
    def __init__(self, connection_id: str, password: str, tcp_port: int):
        self.connection_id = connection_id
        self.password = password
        self.tcp_port = tcp_port
        self.running = threading.Event()
        self.running.set()
        self.sock = None
    
    def run(self):
        """Run the UDP responder to handle discovery requests."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", UDP_PORT))
        
        logging.info(f"UDP responder started on port {UDP_PORT}")
        
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
    def __init__(self, port: int = TCP_PORT):
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
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("0.0.0.0", self.port))
        self.server_socket.listen(5)
        logging.info(f"Server started on 0.0.0.0:{self.port}")
        
        if AUDIO_ENABLED:
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
        
        if AUDIO_ENABLED and self.audio:
            self.audio_thread = threading.Thread(target=self.audio_capture_thread)
            self.audio_thread.daemon = True
            self.audio_thread.start()
        
        # Accept client connections
        accept_thread = threading.Thread(target=self.accept_clients)
        accept_thread.daemon = True
        accept_thread.start()
        
        return True
        
    def stop(self):
        """Stop the server and all threads"""
        self.running.clear()
        
        # Close all client connections
        with self.lock:
            for client in list(self.clients.keys()):
                try:
                    client.close()
                except:
                    pass
            self.clients.clear()
            
        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
                
        # Close audio
        if self.audio:
            try:
                self.audio.terminate()
            except:
                pass
                
        logging.info("Server stopped")
        
    def accept_clients(self):
        """Accept new client connections"""
        while self.running.is_set():
            try:
                self.server_socket.settimeout(1.0)
                client, addr = self.server_socket.accept()
                with self.lock:
                    self.clients[client] = addr
                logging.info(f"New connection from {addr}")
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
                logging.info(f"Connection from {addr} closed")
                
    def send_to_clients(self, message_type: str, data: bytes):
        """Send data to all connected clients with thread safety"""
        header = f"{message_type}:{len(data)}:".encode("utf-8")
        
        with send_lock:  # Use global send lock to prevent concurrent writes
            with self.lock:
                clients_copy = list(self.clients.keys())
                
            for client in clients_copy:
                try:
                    client.sendall(header + data)
                except Exception as e:
                    logging.error(f"Error sending to client: {e}")
                    try:
                        client.close()
                    except:
                        pass
                    self.remove_client(client)
    
    def screen_capture_thread(self):
        """Capture and send screen frames to all clients"""
        while self.running.is_set():
            try:
                # Capture screenshot
                screenshot = pyautogui.screenshot()
                self.screenshot_timestamp = int(time.time() * 1000)  # Milliseconds timestamp
                
                # Convert to bytes
                img_byte_array = cv2.imencode(".jpg", 
                                            np.array(screenshot), 
                                            [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
                
                # Add timestamp to beginning of data
                timestamp_bytes = struct.pack("!Q", self.screenshot_timestamp)
                img_data = timestamp_bytes + img_byte_array
                
                # Send to all clients
                self.send_to_clients("IMG", img_data)
                
                # Sleep to maintain frame rate
                time.sleep(1 / FRAME_RATE)
                
            except Exception as e:
                logging.error(f"Error in screen capture: {e}")
                time.sleep(1)  # Avoid rapid retries on persistent errors
                
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

def signal_handler(sig, frame):
    """Handle system signals for graceful shutdown"""
    logging.info("Shutdown signal received")
    if 'server' in globals() and server is not None:
        server.stop()
    if 'udp_responder' in globals() and udp_responder is not None:
        udp_responder.stop()
    sys.exit(0)

def configure_audio(disable_audio=False):
    """Configure audio settings based on command line args"""
    global AUDIO_ENABLED
    if disable_audio:
        AUDIO_ENABLED = False
        logging.info("Audio capture disabled via command line")
    return AUDIO_ENABLED

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Headless Screen Share Server")
    parser.add_argument("--port", type=int, default=TCP_PORT, 
                        help=f"TCP port for screen sharing (default: {TCP_PORT})")
    parser.add_argument("--discovery-port", type=int, default=UDP_PORT, 
                        help=f"UDP port for discovery (default: {UDP_PORT})")
    parser.add_argument("--connection-id", type=str, default=CONNECTION_ID, 
                        help="Connection ID for discovery")
    parser.add_argument("--password", type=str, default=PASSWORD, 
                        help="Password for discovery")
    parser.add_argument("--no-audio", action="store_true", 
                        help="Disable audio capture")
    args = parser.parse_args()
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Configure audio settings before starting the server
    configure_audio(args.no_audio)
    
    # Print connection info
    print("\n" + "="*50)
    print(f"Connection ID: {args.connection_id}")
    print(f"Password:      {args.password}")
    print(f"TCP Port:      {args.port}")
    print(f"UDP Port:      {args.discovery_port}")
    print("="*50 + "\n")
    
    # Start UDP responder for discovery
    udp_responder = UdpResponder(args.connection_id, args.password, args.port)
    udp_thread = threading.Thread(target=udp_responder.run)
    udp_thread.daemon = True
    udp_thread.start()
    
    # Start screen share server
    global server
    server = ScreenShareServer(args.port)
        
    if not server.start():
        logging.error("Failed to start server")
        sys.exit(1)
        
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        udp_responder.stop()
