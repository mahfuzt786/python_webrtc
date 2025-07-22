#!/usr/bin/env python3
"""Unified Screen-Share Hub

Usage: run this single script on any machine.
• When it launches it shows a Connection ID and Password that it randomly
generates. Share those with the peer.
• If another machine launches the same script and enters that ID+Password,
  it will automatically discover the host on the LAN and connect as a client.

Discovery is handled via a tiny UDP broker built-in: the host listens for
broadcast queries on port 54545 and replies with its IP and TCP port (default
8000). The actual audio/video streaming is performed by the existing
ScreenShareServer / ScreenShareClient classes.

This approach works on the same local network. For WAN/NAT scenarios you would
need to expose the UDP/TCP ports or deploy an external broker.
"""

import os
import random
import socket
import subprocess
import threading
import time
import tkinter as tk
import logging
from tkinter import messagebox
from typing import Optional, Dict, Any, Tuple
from security import safe_command

# Configure logging
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Custom logging filter to remove timeout messages
class LoggingFilter(logging.Filter):
    """Filter to remove socket.timeout messages from logs"""
    def filter(self, record):
        # Filter out timeout messages from the accept_connections method
        return not (record.levelname == 'ERROR' and 'Error accepting connection: timed out' in record.msg)

# Import existing server/client implementations
from python_screen_share_server_with_audio import ScreenShareServer
from python_screen_share_client_with_audio import ScreenShareClient

# Try to import pyngrok for automatic tunnel creation
NGROK_AVAILABLE = False

# Check for cloudflared in common install locations
def find_cloudflared() -> Optional[str]:
    """Find cloudflared executable path. Returns None if not found."""
    # Check if available in PATH
    try:
        result = subprocess.run(["cloudflared", "version"], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.PIPE, 
                              text=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            return "cloudflared"  # Available in PATH
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
        
    # Check common install locations
    possible_paths = [
        r"C:\Program Files\Cloudflare\cloudflared\cloudflared.exe",
        r"C:\Program Files (x86)\Cloudflare\cloudflared\cloudflared.exe",
        os.path.join(os.environ.get("LocalAppData", ""), "Cloudflare", "cloudflared.exe"),
    ]
    
    for path in possible_paths:
        if os.path.isfile(path):
            try:
                result = safe_command.run(subprocess.run, [path, "version"], 
                                      stdout=subprocess.PIPE, 
                                      stderr=subprocess.PIPE,
                                      text=True,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode == 0:
                    return path
            except subprocess.SubprocessError:
                continue
    
    return None

CLOUDFLARED_PATH = find_cloudflared()
CLOUDFLARED_AVAILABLE = CLOUDFLARED_PATH is not None

# --- Constants ----------------------------------------------------------------
UDP_PORT = 54545  # Discovery port
TCP_PORT = 8000  # Screen-share server port (defaults to 8000 in server class)
BROADCAST_ADDR = "255.255.255.255"

# -------------- Helper functions ---------------------------------------------

def generate_id() -> str:
    """Return a random 6-digit connection ID as a string."""
    return f"{random.randint(0, 999999):06d}"

def generate_password() -> str:
    """Return a random 4-digit password."""
    return f"{random.randint(0, 9999):04d}"

def get_local_ip() -> str:
    """Best-effort to obtain the local LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Doesn't need to succeed – no packets sent
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

# ----------------------------- Host UDP responder -----------------------------
class UdpResponder(threading.Thread):
    """Listens for discovery requests and replies with host address."""
    def __init__(self, conn_id: str, password: str, tcp_port: int):
        super().__init__(daemon=True)
        self.conn_id = conn_id
        self.password = password
        self.tcp_port = tcp_port
        self.running = threading.Event()
        self.running.set()
        self.sock: Optional[socket.socket] = None

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Set socket timeout to avoid blocking indefinitely
            self.sock.settimeout(1.0)
            
            try:
                self.sock.bind(("", UDP_PORT))
            except OSError as e:
                print(f"UDP binding error: {e}. Using alternative port.")
                # Try an alternative port if standard port is in use
                self.sock.bind(("", 0))  # Let OS pick an available port
                actual_port = self.sock.getsockname()[1]
                print(f"Bound to alternative UDP port: {actual_port}")
            
            while self.running.is_set():
                try:
                    data, addr = self.sock.recvfrom(1024)
                    payload = data.decode("utf-8", errors="ignore").strip()
                    if payload.startswith("REQ|"):
                        try:
                            _tag, req_id, req_pw = payload.split("|", 2)
                            if req_id == self.conn_id and req_pw == self.password:
                                # Send ACK with our IP and TCP port
                                reply = f"ACK|{get_local_ip()}|{self.tcp_port}"
                                self.sock.sendto(reply.encode("utf-8"), addr)
                        except ValueError:
                            # Invalid format, ignore
                            print(f"Received malformed UDP discovery request")
                except socket.timeout:
                    # This is expected due to the timeout
                    continue
                except ConnectionError as e:
                    print(f"UDP connection error: {e}")
                    continue
                except Exception as e:
                    print(f"UDP error: {e}")
                    continue
        finally:
            if self.sock:
                self.sock.close()

    def stop(self):
        self.running.clear()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

# ------------------------------- GUI (Tkinter) --------------------------------
class HubApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Python Screen Share Hub")
        self.server_thread: Optional[threading.Thread] = None
        self.server: Optional[ScreenShareServer] = None
        self.udp_responder: Optional[UdpResponder] = None
        self.client_app: Optional[ScreenShareClient] = None
        self.cloudflared_process: Optional[subprocess.Popen] = None
        self.tunnel_url: Optional[str] = None
        
        # Add the logging filter to suppress timeout messages
        logging_filter = LoggingFilter()
        logging.getLogger().addFilter(logging_filter)

        # --- UI elements ---
        frame = tk.Frame(root, padx=20, pady=15)
        frame.pack()

        tk.Label(frame, text="Connection ID:").grid(row=0, column=0, sticky="e")
        self.id_var = tk.StringVar(value=generate_id())
        id_entry = tk.Entry(frame, textvariable=self.id_var, width=10, justify="center")
        id_entry.grid(row=0, column=1, pady=4)

        tk.Label(frame, text="Password:").grid(row=1, column=0, sticky="e")
        self.pw_var = tk.StringVar(value=generate_password())
        pw_entry = tk.Entry(frame, textvariable=self.pw_var, width=10, justify="center")
        pw_entry.grid(row=1, column=1, pady=4)

        # Direct host address (for WAN / ngrok / cloudflare)
        tk.Label(frame, text="Host Address:").grid(row=2, column=0, sticky="e")
        self.host_addr_var = tk.StringVar()
        host_entry = tk.Entry(frame, textvariable=self.host_addr_var, width=20, justify="center")
        host_entry.grid(row=2, column=1, pady=4)
        
        # Add helper label for tunneling status
        self.tunnel_status_var = tk.StringVar()
        if NGROK_AVAILABLE:
            self.tunnel_status_var.set("ngrok available")
        elif CLOUDFLARED_AVAILABLE:
            self.tunnel_status_var.set(f"cloudflared found")
        else:
            self.tunnel_status_var.set("No tunneling available")
            
        tk.Label(frame, textvariable=self.tunnel_status_var, fg="gray", font=(None, 8)).grid(row=3, column=0, columnspan=2, pady=0, sticky="w")

        # Buttons
        button_frame = tk.Frame(frame)
        button_frame.grid(row=4, column=0, columnspan=2, pady=10)
        
        host_btn = tk.Button(button_frame, text="Share Screen", command=self.start_host)
        host_btn.pack(side=tk.LEFT, padx=5)

        client_btn = tk.Button(button_frame, text="Connect", command=self.start_client)
        client_btn.pack(side=tk.LEFT, padx=5)

        # Status label
        self.status_var = tk.StringVar(value="Idle")
        tk.Label(frame, textvariable=self.status_var, fg="blue").grid(row=5, column=0, columnspan=2, pady=4)

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- Host side -----------------
    def start_host(self):
        if self.server is not None:
            messagebox.showinfo("Already Hosting", "This instance is already hosting.")
            return
        conn_id = self.id_var.get().strip()
        password = self.pw_var.get().strip()
        if not conn_id or not password:
            messagebox.showerror("Invalid", "ID and password must not be empty.")
            return
        self.status_var.set("Starting server…")
        self.root.update()
        
        # Create credentials from ID and password
        credentials = {'id': conn_id, 'password': password}
        
        # Launch ScreenShareServer in thread
        self.server = ScreenShareServer(host="0.0.0.0", port=TCP_PORT, enable_audio=True)
        # Set credentials for authentication
        self.server.auth_credentials = credentials
        self.server_thread = threading.Thread(target=self.server.start, daemon=True)
        self.server_thread.start()
        
        # Start UDP responder for LAN discovery
        self.udp_responder = UdpResponder(conn_id, password, TCP_PORT)
        self.udp_responder.start()

        public_addr_msg = ""
        # Try tunneling options in order: ngrok, cloudflared
        if NGROK_AVAILABLE:
            try:
                tunnel = ngrok.connect(addr=TCP_PORT, proto="tcp")
                public_url = tunnel.public_url.replace("tcp://", "")
                public_addr_msg = f" | Public: {public_url}"
                # Autofill host address field for convenience
                self.host_addr_var.set(public_url)
                self.tunnel_url = public_url
            except Exception as e:
                public_addr_msg = " | ngrok error"
                print(f"ngrok error: {e}")
        elif CLOUDFLARED_AVAILABLE:
            try:
                # Start cloudflared in a subprocess
                self.status_var.set("Starting Cloudflare Tunnel...")
                self.root.update()
                
                process = safe_command.run(subprocess.Popen, [CLOUDFLARED_PATH, "tunnel", "--url", f"tcp://localhost:{TCP_PORT}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                
                self.cloudflared_process = process
                
                # Wait for the tunnel URL to appear in output
                public_url = None
                start_time = time.time()
                # Read subprocess output for up to 10 seconds to find the tunnel URL
                while time.time() - start_time < 10:
                    line = process.stdout.readline().strip()
                    if not line:
                        time.sleep(0.1)
                        continue
                        
                    print(f"Cloudflared: {line}")
                    
                    # Look for the tunnel address
                    if "https://" in line and ".trycloudflare.com" in line:
                        parts = line.split("https://")
                        if len(parts) > 1:
                            domain = parts[1].split()[0].strip()
                            public_url = f"{domain}:443"
                            break
               
                if public_url:
                    public_addr_msg = f" | Public: {public_url}"
                    # Autofill host address field
                    self.host_addr_var.set(public_url)
                    self.tunnel_url = public_url
                else:
                    public_addr_msg = " | Cloudflare tunnel started (address unknown)"
                    
            except Exception as e:
                public_addr_msg = " | Cloudflare tunnel error"
                print(f"Cloudflared error: {e}")
                if self.cloudflared_process:
                    self.cloudflared_process.terminate()
                    self.cloudflared_process = None
        else:
            public_addr_msg = " | (No tunnel available)"

        self.status_var.set(f"Hosting. Share ID {conn_id}/{password}{public_addr_msg}")

    # ---------------- Client side -----------------
    def start_client(self):
        if self.client_app is not None:
            messagebox.showinfo("Already Connected", "A client session is already running.")
            return
        conn_id = self.id_var.get().strip()
        password = self.pw_var.get().strip()
        if not conn_id or not password:
            messagebox.showerror("Invalid", "Please enter connection ID and password.")
            return

        host_field = self.host_addr_var.get().strip()
        if host_field:
            # Host address provided; connect directly
            if ":" not in host_field:
                messagebox.showerror("Invalid", "Host address must be in host:port form.")
                return
            host_ip, host_port_str = host_field.split(":", 1)
            try:
                host_port = int(host_port_str)
            except ValueError:
                messagebox.showerror("Invalid", "Port must be an integer.")
                return
            self.launch_client(host_ip, host_port)
            return

        # Otherwise, attempt LAN discovery
        self.status_var.set("Finding host…")
        self.root.update()
        host_ip = self.discover_host(conn_id, password)
        if not host_ip:
            self.status_var.set("Host not found.")
            messagebox.showerror("Not Found", "Host with given ID/password not found on LAN.")
            return
        self.launch_client(host_ip, TCP_PORT)

    def launch_client(self, host_ip: str, port: int):
        """Launch the viewer window connected to host_ip:port."""
        # Get current credentials
        conn_id = self.id_var.get().strip()
        password = self.pw_var.get().strip()
        credentials = {'id': conn_id, 'password': password}
        
        # Hide hub window while client runs
        self.root.withdraw()
        client_root = tk.Toplevel()
        client_root.title(f"Screen Share – {host_ip}")
        self.client_app = ScreenShareClient(client_root, server_host=host_ip, server_port=port)
        # Set authentication credentials
        self.client_app.auth_credentials = credentials
        
        def on_client_close():
            self.client_app.disconnect()
            client_root.destroy()
            self.client_app = None
            self.root.deiconify()
            self.status_var.set("Idle")
        client_root.protocol("WM_DELETE_WINDOW", on_client_close)

    # ---------------- Discovery -----------------
    def discover_host(self, conn_id: str, password: str) -> Optional[str]:
        """Broadcast REQ and wait briefly for ACK. Return host IP or None."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(1.0)
            msg = f"REQ|{conn_id}|{password}".encode("utf-8")
            for _ in range(5):  # Retry a few times
                try:
                    s.sendto(msg, (BROADCAST_ADDR, UDP_PORT))
                    data, _ = s.recvfrom(1024)
                    payload = data.decode("utf-8", errors="ignore").strip()
                    if payload.startswith("ACK|"):
                        _tag, ip, port_str = payload.split("|", 2)
                        # Ignore port_str since we use fixed TCP_PORT
                        return ip
                except socket.timeout:
                    continue
                except Exception:
                    break
        return None

    # ---------------- Clean-up -----------------
    def on_close(self):
        if self.client_app is not None:
            self.client_app.disconnect()
        if self.udp_responder is not None:
            self.udp_responder.stop()
        if self.server is not None:
            self.server.stop()
        if self.cloudflared_process is not None:
            self.cloudflared_process.terminate()
            self.cloudflared_process = None
        self.root.destroy()

# --------------------------- Main entry ---------------------------

def main():
    root = tk.Tk()
    HubApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
