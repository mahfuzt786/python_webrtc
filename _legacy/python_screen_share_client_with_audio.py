#!/usr/bin/env python3
"""
Python Screen Share Client with Audio Support
This client connects to a screen sharing server and displays the received screen with audio playback.
"""

import cv2
import numpy as np
import socket
import threading
import time
import pickle
import struct
import logging
import argparse
import tkinter as tk
from tkinter import messagebox, StringVar
from PIL import Image, ImageTk
import pyaudio
import queue

# Configure logging
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

class ScreenShareClient:
    def __init__(self, root, server_host='localhost', server_port=8000):
        """Initialize the screen share client."""
        self.root = root
        self.server_host = server_host
        self.server_port = server_port
        
        # Connection status (initialize before setup_window)
        self.connected = False
        self.socket = None
        self.receive_thread = None
        
        # Audio settings (initialize before setup_window)
        self.audio_enabled = False
        self.audio_rate = 22050
        self.audio_channels = 1
        self.audio_format = pyaudio.paInt16
        self.audio_queue = queue.Queue(maxsize=20)  # Limit queue size to prevent buffer bloat
        self.audio = None
        self.audio_stream = None
        self.volume = 0.5  # Default volume level
        
        # Setup window after attributes are initialized
        self.setup_window()
        
        # Screen dimensions (will be set by server)
        self.screen_width = None
        self.screen_height = None
        
        # Audio settings
        self.audio_enabled = False
        self.audio_rate = 22050
        self.audio_channels = 1
        self.audio_format = pyaudio.paInt16
        self.audio_queue = queue.Queue(maxsize=20)  # Limit queue size to prevent buffer bloat
        self.audio = None
        self.audio_stream = None
        self.volume = 0.5  # Default volume level
        
        # Message types
        self.MSG_VIDEO = 0
        self.MSG_AUDIO = 1
        self.MSG_INFO = 2
        
        # Event to signal shutdown
        self.shutdown_event = threading.Event()
        
        # Volume control
        self.volume = 1.0  # Default volume (1.0 = 100%)
    
    def setup_window(self):
        """Set up the GUI window."""
        self.root.title("Python Screen Share Client with Audio")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Main frame
        main_frame = tk.Frame(self.root, bg="#f0f0f0")
        main_frame.pack(fill="both", expand=True)
        
        # Top frame for controls
        control_frame = tk.Frame(main_frame, bg="#f0f0f0", pady=10)
        control_frame.pack(fill="x")
        
        # Server connection info
        server_label = tk.Label(control_frame, text="Server:", bg="#f0f0f0")
        server_label.pack(side="left", padx=10)
        
        self.server_entry = tk.Entry(control_frame, width=15)
        self.server_entry.insert(0, self.server_host)
        self.server_entry.pack(side="left", padx=(0, 5))
        
        port_label = tk.Label(control_frame, text="Port:", bg="#f0f0f0")
        port_label.pack(side="left")
        
        self.port_entry = tk.Entry(control_frame, width=6)
        self.port_entry.insert(0, str(self.server_port))
        self.port_entry.pack(side="left", padx=(0, 10))
        
        # Connect/Disconnect button
        self.connect_button = tk.Button(
            control_frame,
            text="Connect",
            command=self.toggle_connection,
            bg="#4CAF50",
            fg="white",
            padx=10
        )
        self.connect_button.pack(side="left", padx=10)
        
        # Status message
        self.status_var = StringVar()
        self.status_var.set("Disconnected")
        status_label = tk.Label(
            control_frame,
            textvariable=self.status_var,
            bg="#f0f0f0"
        )
        status_label.pack(side="left", padx=10)
        
        # Audio controls frame
        audio_frame = tk.Frame(main_frame, bg="#f0f0f0", pady=5)
        audio_frame.pack(fill="x")
        
        # Mute/unmute button
        self.audio_status = StringVar()
        self.audio_status.set("🔊")  # Unicode speaker icon
        
        self.mute_button = tk.Button(
            audio_frame,
            textvariable=self.audio_status,
            command=self.toggle_audio,
            width=2,
            bg="#f0f0f0",
            state="disabled"
        )
        self.mute_button.pack(side="left", padx=10)
        
        # Volume slider
        volume_label = tk.Label(audio_frame, text="Volume:", bg="#f0f0f0")
        volume_label.pack(side="left")
        
        self.volume_slider = tk.Scale(
            audio_frame,
            from_=0.0,
            to=1.0,
            resolution=0.01,
            orient="horizontal",
            length=100,
            command=self.set_volume,
            state="disabled",
            bg="#f0f0f0"
        )
        self.volume_slider.set(self.volume)
        self.volume_slider.pack(side="left", padx=5)
        
        # Audio status indicator
        self.audio_indicator = tk.Label(audio_frame, text="Audio: Not Available", bg="#f0f0f0")
        self.audio_indicator.pack(side="left", padx=10)
        
        # Frame for video display
        self.video_frame = tk.Frame(main_frame, bg="#000000")
        self.video_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Canvas for displaying video
        self.canvas = tk.Canvas(self.video_frame, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
    
    def toggle_audio(self):
        """Toggle audio mute/unmute."""
        if not self.audio_enabled or not self.connected:
            return
            
        if self.audio_status.get() == "🔊":  # Currently unmuted
            self.audio_status.set("🔇")  # Unicode mute icon
            self.volume = self.volume_slider.get()
            self.volume_slider.set(0)
        else:  # Currently muted
            self.audio_status.set("🔊")
            self.volume_slider.set(self.volume)
    
    def set_volume(self, value):
        """Set the volume level."""
        self.volume = float(value)
        # Audio stream volume gets applied in the audio_callback function
        
    def toggle_connection(self):
        """Connect to or disconnect from the server."""
        if not self.connected:
            # Get server info from UI
            self.server_host = self.server_entry.get().strip()
            try:
                self.server_port = int(self.port_entry.get().strip())
            except ValueError:
                messagebox.showerror("Invalid Port", "Please enter a valid port number.")
                return
            
            # Connect to server
            self.connect_to_server()
        else:
            # Disconnect
            self.disconnect()
    
    def receive_data(self, timeout=10):
        """Receive a single message from the server and return (msg_type, data)."""
        try:
            # Set timeout for this operation if specified
            if timeout is not None:
                self.socket.settimeout(timeout)
                
            # Buffer for receiving data
            partial_data = b""
            
            # Receive loop for a single complete message
            while True:
                chunk = self.socket.recv(4096)
                if not chunk:
                    raise ConnectionError("Connection closed by server")
                
                # Append to buffer
                partial_data += chunk
                
                # Check if we have a complete message
                if len(partial_data) >= 5:  # Minimum header size
                    # Parse header (type + length)
                    msg_type, msg_len = struct.unpack("!BI", partial_data[:5])
                    
                    # Check if we have the complete message
                    if len(partial_data) >= 5 + msg_len:
                        # Extract message data
                        msg_data = partial_data[5:5+msg_len]
                        return msg_type, msg_data
        
        except socket.timeout:
            raise TimeoutError("Timeout waiting for server message")
        except Exception as e:
            raise ConnectionError(f"Error receiving data: {e}")
        finally:
            # Restore default timeout
            if timeout is not None:
                self.socket.settimeout(None)
    
    def send_data(self, data, msg_type=None):
        """Send data to server with proper message format."""
        if msg_type is None:
            msg_type = self.MSG_INFO  # Default to INFO type for communication
            
        try:
            # Pack message (type + length + data)
            header = struct.pack("!BI", msg_type, len(data))
            message = header + data
            self.socket.sendall(message)
            return True
        except Exception as e:
            logging.error(f"Error sending data: {e}")
            return False
    
    def connect_to_server(self):
        """Connect to the screen sharing server."""
        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)  # Timeout for connection
            
            # Update UI
            self.status_var.set("Connecting...")
            self.root.update()
            
            # Connect
            self.socket.connect((self.server_host, self.server_port))
            
            # Wait for initial messages from server
            try:
                # Handle authentication if server requests it
                msg_type, data = self.receive_data(timeout=10)
                
                if msg_type == self.MSG_INFO:
                    info = pickle.loads(data)
                    
                    # Authentication protocol
                    if isinstance(info, dict) and info.get('type') == 'auth_request':
                        # Send credentials to server
                        auth_credentials = getattr(self, 'auth_credentials', None)
                        logging.info(f"Server requested authentication, sending credentials: {auth_credentials is not None}")
                        
                        auth_response = {
                            'type': 'auth',
                            'credentials': auth_credentials
                        }
                        self.send_data(pickle.dumps(auth_response))
                        
                        # Wait for auth result
                        msg_type, data = self.receive_data(timeout=10)
                        if msg_type == self.MSG_INFO:
                            auth_result = pickle.loads(data)
                            if isinstance(auth_result, dict) and auth_result.get('type') == 'auth_result':
                                if not auth_result.get('success', False):
                                    logging.error("Authentication failed. Invalid credentials.")
                                    self.status_var.set("Authentication failed.")
                                    self.disconnect()
                                    return
                                else:
                                    logging.info("Authentication successful")
                        else:
                            logging.error("Unexpected message type during authentication.")
                            self.status_var.set("Authentication protocol error.")
                            self.disconnect()
                            return
                    
                    # Get initial screen info
                    msg_type, data = self.receive_data(timeout=10)
                    if msg_type == self.MSG_INFO:
                        info = pickle.loads(data)
                        self.screen_width = info['width']
                        self.screen_height = info['height']
                        self.audio_enabled = info.get('audio_enabled', False)
                        
                        if self.audio_enabled:
                            self.init_audio(
                                info.get('audio_rate', 22050),
                                info.get('audio_channels', 1),
                                info.get('audio_format', 'int16')
                            )
                
            except (TimeoutError, ConnectionError) as e:
                logging.error(f"Error during initial connection: {e}")
                self.status_var.set(f"Connection failed: {e}")
                self.disconnect()
                return
            # Set socket timeout back to None for continuous streaming
            self.socket.settimeout(None)
                
            # Start receiving thread
            self.connected = True
            self.shutdown_event.clear()
            self.receive_thread = threading.Thread(target=self.receive_stream)
            self.receive_thread.daemon = True
            self.receive_thread.start()
            
            # Update UI
            self.connect_button.config(text="Disconnect", bg="#f44336")
            self.status_var.set("Connected")
            
        except Exception as e:
            logging.error(f"Connection error: {e}")
            self.status_var.set(f"Connection failed: {e}")
            
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
    
    def disconnect(self):
        """Disconnect from the server."""
        if self.connected:
            # Signal thread to stop
            self.shutdown_event.set()
            self.connected = False
            
            # Close socket
            if self.socket:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                    self.socket.close()
                except:
                    pass
                finally:
                    self.socket = None
            
            # Stop audio
            self.stop_audio()
                
            # Update UI
            self.connect_button.config(text="Connect", bg="#4CAF50")
            self.status_var.set("Disconnected")
            
            # Disable audio controls
            self.mute_button.config(state="disabled")
            self.volume_slider.config(state="disabled")
            self.audio_indicator.config(text="Audio: Not Available")
            
            # Clear screen
            self.canvas.delete("all")
    
    def init_audio(self, rate, channels, format_str):
        """Initialize audio playback."""
        try:
            # Convert format string to PyAudio format
            if format_str == 'int16':
                audio_format = pyaudio.paInt16
            elif format_str == 'int8':
                audio_format = pyaudio.paInt8
            elif format_str == 'float32':
                audio_format = pyaudio.paFloat32
            else:
                audio_format = pyaudio.paInt16
            
            # Initialize PyAudio
            self.audio = pyaudio.PyAudio()
            
            # Open output stream
            self.audio_stream = self.audio.open(
                format=audio_format,
                channels=channels,
                rate=rate,
                output=True,
                frames_per_buffer=1024,
                stream_callback=self.audio_callback
            )
            
            # Enable audio controls
            self.root.after(0, self.enable_audio_controls)
            
            return True
            
        except Exception as e:
            logging.error(f"Could not initialize audio: {e}")
            return False
    
    def enable_audio_controls(self):
        """Enable audio controls in UI."""
        self.mute_button.config(state="normal")
        self.volume_slider.config(state="normal")
        self.audio_indicator.config(text="Audio: Active")
    
    def stop_audio(self):
        """Stop audio playback."""
        if self.audio_stream:
            try:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
            except:
                pass
        
        if self.audio:
            try:
                self.audio.terminate()
            except:
                pass
        
        self.audio_stream = None
        self.audio = None
        self.audio_enabled = False
    
    def audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback for audio playback."""
        if self.shutdown_event.is_set():
            return (None, pyaudio.paComplete)
        
        try:
            data = self.audio_queue.get_nowait()
            
            # Apply volume control if needed
            if self.volume < 1.0:
                # Convert bytes to numpy array
                samples = np.frombuffer(data, dtype=np.int16)
                # Apply volume
                samples = (samples * self.volume).astype(np.int16)
                # Convert back to bytes
                data = samples.tobytes()
                
            return (data, pyaudio.paContinue)
        except queue.Empty:
            # If queue is empty, return silence
            return (bytes(frame_count * self.audio_channels * 2), pyaudio.paContinue)
    
    def receive_stream(self):
        """Receive and handle the stream data."""
        try:
            # Buffer for receiving data
            partial_data = b""
            
            # Main receive loop
            while self.connected and not self.shutdown_event.is_set():
                try:
                    chunk = self.socket.recv(4096)
                    if not chunk:
                        break
                    
                    # Append to buffer
                    partial_data += chunk
                    
                    # Process complete messages
                    while len(partial_data) >= 5:  # Minimum header size
                        # Parse header (type + length)
                        msg_type, msg_len = struct.unpack("!BI", partial_data[:5])
                        
                        # Check if we have the complete message
                        if len(partial_data) < 5 + msg_len:
                            break
                            
                        # Extract message data
                        msg_data = partial_data[5:5+msg_len]
                        
                        # Handle message based on type
                        if msg_type == self.MSG_VIDEO:
                            self.handle_video_frame(msg_data)
                        elif msg_type == self.MSG_AUDIO:
                            self.handle_audio_data(msg_data)
                        elif msg_type == self.MSG_INFO:
                            self.handle_info_data(msg_data)
                        else:
                            logging.warning(f"Unknown message type: {msg_type}")
                        
                        # Remove processed message from buffer
                        partial_data = partial_data[5+msg_len:]
                
                except socket.timeout:
                    continue
                except Exception as e:
                    logging.error(f"Error receiving data: {e}")
                    break
        
        except Exception as e:
            logging.error(f"Receive thread error: {e}")
        
        finally:
            # Disconnect if not already disconnecting
            if self.connected and not self.shutdown_event.is_set():
                self.root.after(0, self.handle_disconnect, "Connection lost")
    
    def handle_video_frame(self, frame_data):
        """Handle received video frame."""
        try:
            # Convert to image
            frame = cv2.imdecode(
                np.frombuffer(frame_data, np.uint8),
                cv2.IMREAD_COLOR
            )
            
            # Convert to RGB format
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Display on canvas
            self.root.after(0, lambda: self.display_frame(frame))
            
        except Exception as e:
            logging.error(f"Error processing video frame: {e}")
    
    def handle_audio_data(self, audio_data):
        """Handle received audio data."""
        if not self.audio_enabled or not self.audio_stream:
            return
            
        try:
            # Add to audio queue, discard if queue full
            try:
                self.audio_queue.put_nowait(audio_data)
            except queue.Full:
                pass  # Discard audio data if queue is full
                
        except Exception as e:
            logging.error(f"Error processing audio data: {e}")
    
    def handle_info_data(self, info_data):
        """Handle server info data."""
        try:
            # Unpack info
            info = pickle.loads(info_data)
            
            # Set screen dimensions
            self.screen_width = info.get('width')
            self.screen_height = info.get('height')
            
            # Set audio info
            self.audio_enabled = info.get('audio_enabled', False)
            
            if self.audio_enabled:
                # Initialize audio playback
                rate = info.get('audio_rate', 22050)
                channels = info.get('audio_channels', 1)
                format_str = info.get('audio_format', 'int16')
                
                # Setup audio on UI thread
                self.root.after(0, lambda: self.init_audio(rate, channels, format_str))
                logging.info(f"Audio enabled: {rate}Hz, {channels} channels, {format_str}")
            else:
                # Update UI to show audio status
                self.root.after(0, lambda: self.audio_indicator.config(text="Audio: Not Available"))
                logging.info("Audio not enabled on server")
            
            logging.info(f"Received server info: {info}")
            
        except Exception as e:
            logging.error(f"Error processing server info: {e}")
    
    def display_frame(self, frame):
        """Display the received frame on the canvas."""
        # Get current canvas dimensions
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width <= 1 or canvas_height <= 1:
            # Canvas not ready yet, try again later
            self.root.after(100, lambda: self.display_frame(frame))
            return
        
        # Ensure screen dimensions are known
        if self.screen_width is None or self.screen_height is None:
            # Infer from current frame
            self.screen_height, self.screen_width = frame.shape[:2]
            logging.debug("Screen dimensions inferred from first frame: %sx%s", self.screen_width, self.screen_height)

        # Resize frame to fit canvas while maintaining aspect ratio
        aspect_ratio = self.screen_width / self.screen_height
        canvas_ratio = canvas_width / canvas_height
        
        if aspect_ratio > canvas_ratio:
            # Fit to width
            display_width = canvas_width
            display_height = int(display_width / aspect_ratio)
        else:
            # Fit to height
            display_height = canvas_height
            display_width = int(display_height * aspect_ratio)
        
        # Resize the frame
        resized = cv2.resize(frame, (display_width, display_height))
        
        # Convert to PhotoImage
        img = Image.fromarray(resized)
        photo = ImageTk.PhotoImage(image=img)
        
        # Update canvas
        self.canvas.delete("all")
        self.canvas.create_image(
            canvas_width/2, canvas_height/2, 
            image=photo, anchor=tk.CENTER
        )
        
        # Keep a reference to prevent garbage collection
        self.photo = photo
    
    def handle_disconnect(self, error_message):
        """Handle server disconnection."""
        if self.connected:
            self.disconnect()
            messagebox.showwarning("Connection Lost", f"Lost connection to server: {error_message}")
    
    def on_closing(self):
        """Handle window closing."""
        if self.connected:
            if messagebox.askyesno("Quit", "Disconnect and quit?"):
                self.disconnect()
                self.root.destroy()
        else:
            self.root.destroy()

def create_argument_parser():
    """Create command line argument parser."""
    parser = argparse.ArgumentParser(description="Python Screen Share Client with Audio")
    parser.add_argument("--host", default="127.0.0.1", help="Server IP/hostname (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument("--id", dest="conn_id", help="Connection ID / Username", default=None)
    parser.add_argument("--password", dest="conn_password", help="Connection password", default=None)
    return parser

def main():
    """Main entry point."""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    root = tk.Tk()
    client_app = ScreenShareClient(root, server_host=args.host, server_port=args.port)

    # Attach credentials if provided
    if args.conn_id is not None or args.conn_password is not None:
        client_app.auth_credentials = {
            "id": args.conn_id,
            "password": args.conn_password,
        }
    else:
        # Ensure attribute exists to prevent getattr errors
        client_app.auth_credentials = None

    # Auto-attempt connection on launch when CLI args are supplied
    if args.host or args.port:
        client_app.toggle_connection()

    root.mainloop()

if __name__ == "__main__":
    main()
