#!/usr/bin/env python3
"""
Python Screen Sharing Server with Audio Support
This server captures the screen without cursor and system audio, then streams both to connected clients.
"""

import cv2
import numpy as np
import pyautogui
import socket
import threading
import time
import pickle
import struct
import logging
import argparse
import pyaudio
import queue
from datetime import datetime

# Configure logging
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Audio constants
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 22050  # Reduced from 44100 to save bandwidth

class ScreenShareServer:
    def __init__(self, host='0.0.0.0', port=8000, fps=15, quality=50, scale=0.75,
                 enable_audio=True, audio_quality=5):
        """Initialize the screen sharing server."""
        self.host = host
        self.port = port
        self.fps = fps
        self.quality = quality  # JPEG compression quality (0-100)
        self.scale = scale  # Scaling factor for the screen capture
        self.enable_audio = enable_audio
        self.audio_quality = audio_quality  # 1-10, affects compression and sampling rate
        
        self.running = False
        self.clients = []
        self.clients_lock = threading.Lock()
        
        # Lock to serialize writes to each socket and avoid message interleaving
        self.send_lock = threading.Lock()
        
        # Get screen size
        self.screen_size = pyautogui.size()
        self.scaled_width = int(self.screen_size.width * self.scale)
        self.scaled_height = int(self.screen_size.height * self.scale)
        
        # Socket for accepting new connections
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Audio setup
        self.audio_queue = queue.Queue()
        if self.enable_audio:
            self.init_audio()
        
        # Message types
        self.MSG_VIDEO = 0
        self.MSG_AUDIO = 1
        self.MSG_INFO = 2
    
    def init_audio(self):
        """Initialize audio capture."""
        try:
            self.audio = pyaudio.PyAudio()
            self.audio_stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
                stream_callback=self.audio_callback
            )
            logging.info("Audio capture initialized")
        except Exception as e:
            logging.error(f"Could not initialize audio: {e}")
            self.enable_audio = False
    
    def audio_callback(self, in_data, frame_count, time_info, status):
        """Callback from PyAudio when audio data is available."""
        self.audio_queue.put(in_data)
        return (None, pyaudio.paContinue)
    
    def start(self):
        """Start the screen sharing server."""
        if self.running:
            logging.warning("Server is already running")
            return
        
        try:
            # Bind and listen
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            self.running = True
            
            logging.info(f"Server started on {self.host}:{self.port}")
            logging.info(f"Audio support: {'Enabled' if self.enable_audio else 'Disabled'}")
            
            # Start threads
            self.accept_thread = threading.Thread(target=self.accept_connections)
            self.accept_thread.daemon = True
            self.accept_thread.start()
            
            self.capture_thread = threading.Thread(target=self.capture_and_stream)
            self.capture_thread.daemon = True
            self.capture_thread.start()
            
            if self.enable_audio:
                self.audio_thread = threading.Thread(target=self.process_audio)
                self.audio_thread.daemon = True
                self.audio_thread.start()
            
            # Keep main thread alive
            try:
                while self.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()
        
        except Exception as e:
            logging.error(f"Error starting server: {e}")
            self.stop()
    
    def stop(self):
        """Stop the screen sharing server."""
        self.running = False
        
        # Stop audio
        if self.enable_audio:
            try:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
                self.audio.terminate()
            except:
                pass
        
        # Close all client connections
        with self.clients_lock:
            for client in self.clients:
                try:
                    client.close()
                except:
                    pass
            self.clients.clear()
        
        # Close server socket
        try:
            self.server_socket.close()
        except:
            pass
        
        logging.info("Server stopped")
    
    def accept_connections(self):
        """Accept incoming client connections."""
        self.server_socket.settimeout(1.0)  # Add timeout to prevent blocking indefinitely
        
        while self.running:
            try:
                client_socket, addr = self.server_socket.accept()
                # Set TCP keepalive to detect dead connections
                client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                
                # Do NOT add client to streaming list yet; wait until authentication succeeds
                logging.info(f"Client connected from {addr[0]}:{addr[1]}")
                

                # Start a thread to handle client messages
                client_thread = threading.Thread(target=self.handle_client, args=(client_socket, addr))
                client_thread.daemon = True
                client_thread.start()
                
            except socket.timeout:
                # This is expected behavior with the non-blocking socket
                # Don't log timeouts as they're part of normal operation
                pass
            except Exception as e:
                if self.running:
                    logging.error(f"Error accepting connection: {e}")
    
    def handle_client(self, client_socket, addr):
        """Handle a client connection."""
        logging.info(f"Handling client {addr}")
        
        try:
            # Set socket timeout to detect dead connections
            client_socket.settimeout(5.0)
            
            # Authentication flag
            authenticated = False
            auth_attempts = 0
            max_auth_attempts = 3
            
            # Send auth request to client
            auth_request = {'type': 'auth_request'}
            self.send_data(client_socket, pickle.dumps(auth_request), self.MSG_INFO)
            logging.info(f"Sent authentication request to client {addr}")
            
            while self.running:
                try:
                    # Use the new receive_data method with proper message framing
                    msg_type, data = self.receive_data(client_socket, 10)
                    
                    # Process messages based on msg_type
                    if msg_type == self.MSG_INFO:
                        try:
                            message = pickle.loads(data)
                            logging.info(f"Received message from client {addr}: {message}")
                            
                            # Authentication handling
                            if not authenticated:
                                if isinstance(message, dict) and message.get('type') == 'auth':
                                    auth_attempts += 1
                                    
                                    # Validate credentials
                                    expected_creds = getattr(self, 'auth_credentials', None)
                                    if expected_creds is None or message.get('credentials') == expected_creds:
                                        authenticated = True
                                        # Send auth success
                                        auth_response = {'type': 'auth_result', 'success': True}
                                        self.send_data(client_socket, pickle.dumps(auth_response), self.MSG_INFO)
                                        logging.info(f"Client {addr} authenticated successfully")

                                        # Now that client is authenticated, add to streaming list
                                        with self.clients_lock:
                                            if client_socket not in self.clients:
                                                self.clients.append(client_socket)

                                        # Send initial screen/audio info after authentication
                                        info = {
                                            'width': self.scaled_width,
                                            'height': self.scaled_height,
                                            'audio_enabled': self.enable_audio,
                                            'audio_rate': RATE,
                                            'audio_channels': CHANNELS,
                                            'audio_format': 'int16'
                                        }
                                        self.send_data(client_socket, pickle.dumps(info), self.MSG_INFO)
                                    else:
                                        # Failed authentication
                                        auth_response = {'type': 'auth_result', 'success': False}
                                        self.send_data(client_socket, pickle.dumps(auth_response), self.MSG_INFO)
                                        logging.warning(f"Client {addr} failed authentication attempt {auth_attempts}/{max_auth_attempts}")
                                        
                                        if auth_attempts >= max_auth_attempts:
                                            logging.warning(f"Client {addr} exceeded maximum auth attempts, disconnecting")
                                            break
                                else:
                                    # Ignore non-auth messages from unauthenticated clients
                                    logging.warning(f"Received non-auth message from unauthenticated client {addr}")
                            else:
                                # Handle commands from authenticated clients
                                # Additional command handling can go here
                                pass
                        except Exception as e:
                            logging.error(f"Error processing client message: {e}")
                    else:
                        # Handle other message types
                        pass
                except socket.timeout:
                    # Socket timeout - check if client is still connected
                    continue
                except ConnectionError as e:
                    logging.info(f"Connection error with client {addr}: {e}")
                    break
                except (TimeoutError, Exception) as e:
                    logging.error(f"Error receiving from client {addr}: {e}")
                    break
        
        except Exception as e:
            logging.error(f"Error handling client {addr}: {e}")
        
        finally:
            # Remove client from list when they disconnect
            with self.clients_lock:
                if client_socket in self.clients:
                    self.clients.remove(client_socket)
                    logging.info(f"Client disconnected: {addr[0]}:{addr[1]}")
                    
                    try:
                        client_socket.close()
                    except:
                        pass
    
    def capture_and_stream(self):
        """Capture screen and stream to connected clients."""
        prev_time = time.time()
        
        while self.running:
            try:
                # Control frame rate
                time_elapsed = time.time() - prev_time
                if time_elapsed < 1.0/self.fps:
                    time.sleep(1.0/self.fps - time_elapsed)
                
                prev_time = time.time()
                
                # Skip if no clients are connected
                with self.clients_lock:
                    if not self.clients:
                        time.sleep(0.1)
                        continue
                
                # Capture screen (without cursor)
                img = pyautogui.screenshot()
                
                # Convert to numpy array
                frame = np.array(img)
                
                # Scale down to reduce bandwidth
                if self.scale != 1.0:
                    frame = cv2.resize(frame, (self.scaled_width, self.scaled_height))
                
                # Convert from BGR to RGB
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Compress the frame using JPEG
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
                _, buffer = cv2.imencode('.jpg', frame, encode_param)
                
                # Send to all clients
                self.send_to_all_clients(buffer.tobytes(), self.MSG_VIDEO)
            
            except Exception as e:
                logging.error(f"Error in capture and stream: {e}")
                time.sleep(0.5)  # Wait a bit before retrying
    
    def process_audio(self):
        """Process and send audio data to clients."""
        if not self.enable_audio:
            return
            
        while self.running:
            try:
                # Get data from audio queue
                try:
                    audio_data = self.audio_queue.get(timeout=1)
                except queue.Empty:
                    continue
                
                # Here you could apply audio compression
                # For simplicity, we'll just send the raw audio for now
                # In a more advanced implementation, you could use audio codecs
                
                # Skip if no clients
                with self.clients_lock:
                    if not self.clients:
                        continue
                        
                # Send to all clients
                self.send_to_all_clients(audio_data, self.MSG_AUDIO)
                
            except Exception as e:
                logging.error(f"Error processing audio: {e}")
                time.sleep(0.1)
    
    def send_to_all_clients(self, data, msg_type):
        """Send data to all connected clients."""
        with self.clients_lock:
            disconnected_clients = []
            
            for client in self.clients:
                # Use the improved send_data method that returns success/failure
                if not self.send_data(client, data, msg_type):
                    disconnected_clients.append(client)
            
            # Remove disconnected clients
            if disconnected_clients:
                for client in disconnected_clients:
                    if client in self.clients:
                        self.clients.remove(client)
                        logging.info(f"Removed disconnected client from list")
                        try:
                            client.close()
                        except Exception as e:
                            logging.debug(f"Error closing client socket: {e}")
                logging.info(f"Remaining connected clients: {len(self.clients)}")

    
    def send_data(self, sock, data, msg_type):
        """Send data to a client with a message type and length prefix."""
        # Format: [type: 1 byte][length: 4 bytes][data]
        message = struct.pack("!BI", msg_type, len(data)) + data
        
        # Ensure atomic send to prevent interleaving between threads
        try:
            with self.send_lock:
                sock.sendall(message)
            return True
        except ConnectionError as e:
            logging.error(f"Connection error when sending data: {e}")
            # Remove client on next send_to_all_clients call
            return False
        except Exception as e:
            logging.error(f"Error sending data: {e}")
            return False
            
    def receive_data(self, sock, timeout=10):
        """Receive a single message from a client and return (msg_type, data)."""
        try:
            # Set timeout for this operation if specified
            if timeout is not None:
                sock.settimeout(timeout)
                
            # Buffer for receiving data
            partial_data = b""
            
            # Receive loop for a single complete message
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("Connection closed by client")
                
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
            raise TimeoutError("Timeout waiting for client message")
        except Exception as e:
            raise ConnectionError(f"Error receiving data: {e}")
        finally:
            # Restore default timeout
            if timeout is not None:
                sock.settimeout(None)

def create_argument_parser():
    """Create command line argument parser."""
    parser = argparse.ArgumentParser(description="Python Screen Sharing Server with Audio")
    parser.add_argument("--host", default="0.0.0.0", help="Hostname to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second")
    parser.add_argument("--quality", type=int, default=70, help="JPEG compression quality (0-100)")
    parser.add_argument("--scale", type=float, default=0.75, help="Screen scaling factor")
    parser.add_argument("--no-audio", action="store_true", help="Disable audio capture")
    parser.add_argument("--audio-quality", type=int, default=5, help="Audio quality (1-10)")
    return parser

def main():
    """Main entry point."""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Create and start server
    server = ScreenShareServer(
        host=args.host,
        port=args.port,
        fps=args.fps,
        quality=args.quality,
        scale=args.scale,
        enable_audio=not args.no_audio,
        audio_quality=args.audio_quality
    )
    
    try:
        print(f"Starting screen sharing server on {args.host}:{args.port}")
        print(f"Audio capture is {'disabled' if args.no_audio else 'enabled'}")
        print("Press Ctrl+C to stop the server")
        server.start()
    except KeyboardInterrupt:
        print("Shutting down...")
        server.stop()

if __name__ == "__main__":
    main()
