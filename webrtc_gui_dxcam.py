#!/usr/bin/env python3
"""WebRTC Screen Share Hub (single-script host / client)

Run this one file on any machine.  Choose
  • "Share Screen" to become the host, or
  • "Connect" to join as a viewer.

Only WebRTC is used – no TCP tunnels / ngrok / cloudflared.
Currently streams video; audio will be re-enabled in a later update.
"""

import asyncio
import fractions
import logging
import random
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable

import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np
import mss
import av
import pyaudio
import psutil
_process = psutil.Process()
try:
    import GPUtil as _gputil
except ImportError:
    _gputil = None  # GPU usage not available

# ---------------------------------------------------------------------------
# Hardware encoder patch: prefer NVENC / QuickSync / VideoToolbox if available
# We monkey-patch aiortc's H264Encoder to create a GPU encoder instead of the
# default libx264 software encoder.
# ---------------------------------------------------------------------------
try:
    import fractions
    from aiortc.codecs.h264 import H264Encoder, MAX_FRAME_RATE
    import logging as _hwlog

    def _encode_frame_hw(self, frame, force_keyframe):
        """Replacement for H264Encoder._encode_frame that prefers GPU encoders."""
        # -- original cache-invalidating logic ---------------------------------
        adj_w = frame.width  - (frame.width  % 2)
        adj_h = frame.height - (frame.height % 2)

        if self.codec and (
             adj_w != self.codec.width
             or adj_h != self.codec.height
        ):
            self.buffer_data = b""
            self.buffer_pts = None
            self.codec = None

        import av  # local import keeps top-level clean
        try:
            from av.error import AVError  # PyAV <10
        except ImportError:
            AVError = getattr(av, "AVError", Exception)  # Fallback if symbol missing
        from av.video.frame import PictureType

        # Ensure even dimensions (NVENC requires width & height multiples of 2)
        if (adj_w, adj_h) != (frame.width, frame.height):
            frame = frame.reformat(width=adj_w, height=adj_h)

        # -- force keyframe if requested --------------------------------------
        frame.pict_type = PictureType.I if force_keyframe else PictureType.NONE

        # -- (re)create codec context -----------------------------------------
        if self.codec is None:
            enc_order = ("h264_nvenc", "h264_qsv", "h264_videotoolbox")
            if getattr(self, "_hw_failed", False):
                enc_order = ()  # skip HW encoders after first failure
            for enc_name in (*enc_order, "libx264"):
                try:
                    self.codec = av.CodecContext.create(enc_name, "w")
                    print(f"H.264 encoder selected: {enc_name}")
                except (AVError, Exception):
                    continue
            if self.codec is None:
                raise RuntimeError("No suitable H.264 encoder found (NVENC/QSV/VTB/libx264 none available)")

            # basic codec settings (copied from original implementation)
            self.codec.width = adj_w
            self.codec.height = adj_h
            self.codec.bit_rate = self.target_bitrate
            self.codec.pix_fmt = "yuv420p"
            self.codec.framerate = fractions.Fraction(MAX_FRAME_RATE, 1)
            self.codec.time_base = fractions.Fraction(1, MAX_FRAME_RATE)
            self.codec.options = {"level": "31", "tune": "zerolatency"}
            self.codec.profile = "Baseline"

        data_to_send = b""
        try:
            for packet in self.codec.encode(frame):
                data_to_send += bytes(packet)
        except (AVError, ValueError, Exception) as encode_err:
            # Hardware encoder failed, fall back to libx264 once
            print(f"[WARN] Encoder {self.codec.name} failed: {encode_err}. Falling back to libx264.")
            self._hw_failed = True  # remember hardware failure
            try:
                self.codec = av.CodecContext.create("libx264", "w")
                print("H.264 encoder selected: libx264 (fallback)")
                self.codec.width = adj_w
                self.codec.height = adj_h
                self.codec.bit_rate = self.target_bitrate
                self.codec.pix_fmt = "yuv420p"
                self.codec.framerate = fractions.Fraction(MAX_FRAME_RATE, 1)
                self.codec.time_base = fractions.Fraction(1, MAX_FRAME_RATE)
                self.codec.options = {"preset": "ultrafast", "tune": "zerolatency"}
                self.codec.profile = "baseline"
                print("H.264 encoder selected: libx264 (fallback)")
                for packet in self.codec.encode(frame):
                    data_to_send += bytes(packet)
            except Exception as fallback_err:
                print(f"[ERROR] libx264 fallback also failed: {fallback_err}")
                raise

        if data_to_send:
            yield from self._split_bitstream(data_to_send)

    # apply monkey-patch
    H264Encoder._encode_frame = _encode_frame_hw  # type: ignore[assignment]
    print("Hardware H.264 encoder patch active (NVENC/QSV/VTB if present)")
except Exception as _e:
    print("[WARN] Hardware encoder patch failed:", _e)
# ---------------------------------------------------------------------------

import urllib.request, json
from aiortc.mediastreams import MediaStreamError
from aiohttp import ClientSession, WSMsgType
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    AudioStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCRtpSender,
)

# ---------------------------------------------------------------------------
# Helper to fetch public IP

def get_public_ip() -> str | None:
    """Return public IP address as string or None if unavailable."""
    try:
        with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=3) as resp:
            return json.loads(resp.read().decode()).get("ip")
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hub")

# ---------------------------------------------------------------------------
# Audio capture constants
AUDIO_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_FORMAT = pyaudio.paInt16

# ---------------------------------------------------------------------------
# Screen Capture Track
class ScreenTrack(VideoStreamTrack):
    """A video stream track that captures the local screen."""

    def __init__(self, capture_fps: int = 60, send_fps: int = 30, scale: float = 0.5):
        super().__init__()
        self.capture_fps = capture_fps
        self.send_fps = send_fps
        self.scale = scale
        self._ratio = max(1, int(capture_fps / send_fps))
        self._capture_counter = 0
        self._sent_counter = 0
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[1]  # Primary monitor

        import platform
        self.use_dxcam = False
        if platform.system() == "Windows":
            try:
                import dxcam  # Requires dxcam>=0.0.8
                self.camera = dxcam.create(output_idx=0)
                if hasattr(self.camera, 'start'):
                    if hasattr(self.camera.start, '__code__') and 'target_fps' in self.camera.start.__code__.co_varnames:
                        self.camera.start(target_fps=capture_fps)
                    else:
                        self.camera.start()
                self.use_dxcam = True
                print("Using DXGI Desktop Duplication via dxcam")
            except Exception as e:
                print(f"dxcam unavailable, falling back to MSS: {e}")

        # Pre-compute consistent output dimensions (force even numbers)
        src_w, src_h = self.monitor["width"], self.monitor["height"]
        out_w = max(2, int(src_w * self.scale))
        out_h = max(2, int(src_h * self.scale))
        out_w -= out_w % 2
        out_h -= out_h % 2
        self.out_size = (out_w, out_h)  # (width, height)

        self.use_gpu = False
        self.gpu_frame = None
        try:
            if cv2.cuda.getCudaEnabledDeviceCount() > 0:
                self.use_gpu = True
                print("Enabling GPU acceleration")
        except:
            pass

    async def recv(self):
        start = time.time()
        
        # Only skip frame if we're behind
        skip_frame = (self._capture_counter % self._ratio) != 0
        self._capture_counter += 1
        
        if not skip_frame:
            # Capture frame (dxcam on Windows, else MSS)
            if self.use_dxcam:
                frame_np = self.camera.get_latest_frame()
                if frame_np is None:
                    await asyncio.sleep(0)
                    return await self.recv()
            else:
                import platform
                if platform.system() == "Windows":
                    # Synchronous grab on Windows to avoid GDI thread-local handle issue
                    raw = self.sct.grab(self.monitor)
                else:
                    # Non-blocking capture on other OSes
                    raw = await asyncio.to_thread(self.sct.grab, self.monitor)
                frame_np = np.frombuffer(raw.raw, dtype=np.uint8).reshape(raw.height, raw.width, 4)

            # Optional resize
            if self.scale != 1.0:
                if self.use_gpu:
                    if self.gpu_frame is None:
                        self.gpu_frame = cv2.cuda_GpuMat()
                    self.gpu_frame.upload(frame_np)
                    resized = cv2.cuda.resize(self.gpu_frame, self.out_size)
                    frame_np = resized.download()
                else:
                    frame_np = cv2.resize(frame_np, self.out_size, interpolation=cv2.INTER_AREA)

            # Build VideoFrame from numpy array (dxcam returns BGR, mss returns BGRA)
            if frame_np.shape[2] == 3:
                src_fmt = "bgr24"
            else:
                src_fmt = "bgra"
            av_frame = (
                av.VideoFrame
                .from_ndarray(frame_np, format=src_fmt)
                .reformat(format="yuv420p")
            )

            av_frame.pts = self._sent_counter
            av_frame.time_base = fractions.Fraction(1, self.send_fps)
            self._sent_counter += 1
            return av_frame
        
        # Yield control briefly when skipping frame
        await asyncio.sleep(0)
        return None

# ---------------------------------------------------------------------------
# System Audio Track
class SystemAudioTrack(AudioStreamTrack):
    """Capture system audio via PyAudio and expose as AudioStreamTrack."""

    def __init__(self, chunk: int = 1024):
        super().__init__()
        self.chunk = chunk
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=AUDIO_FORMAT,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            input=True,
            frames_per_buffer=self.chunk,
        )
        self._pts = 0

    async def recv(self):
        data = await asyncio.get_event_loop().run_in_executor(
            None, self.stream.read, self.chunk, False
        )
        samples = np.frombuffer(data, dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = AUDIO_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, AUDIO_RATE)
        self._pts += samples.shape[0]
        return frame

    def stop(self):
        super().stop()
        try:
            self.stream.stop_stream()
            self.stream.close()
            self.p.terminate()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Helper dataclasses
@dataclass
class Credentials:
    conn_id: str
    password: str

@dataclass
class Settings:
    signalling_url: str
    fps: int
    scale: float

# ---------------------------------------------------------------------------
# Viewer waiting notifier ------------------------------------------------
async def notify_viewer_waiting(settings: Settings, creds: Credentials):
    """Client helper: send a viewer_waiting message after auth so host sees it."""
    async with ClientSession() as session:
        async with session.ws_connect(settings.signalling_url) as ws:
            await ws.send_json({"type": "viewer_waiting", "id": creds.conn_id})

# ---------------------------------------------------------------------------
# WebRTC logic (host and client) ------------------------------------------------

ICE_CONFIG = RTCConfiguration(
    iceServers=[
        RTCIceServer("stun:stun.l.google.com:19302"),
        RTCIceServer(
            "turn:turn4.viagenie.ca",
            username="webrtc@live.com",
            credential="muazkh",
        ),
        RTCIceServer(
            "turn:numb.viagenie.ca",
            username="webrtc@live.com",
            credential="muazkh",
        ),
        RTCIceServer(
            "turn:xturn.readybill.app",
            username="firedrake",
            credential="toortoor",
        ),
    ]
)

async def run_host(settings: Settings, creds: Credentials, status_cb: Callable[[str], None]):
    """Run as WebRTC host (share screen)."""
    async with ClientSession() as session:
        async with session.ws_connect(settings.signalling_url) as ws:
            # Send auth
            await ws.send_json({"type": "auth", "id": creds.conn_id, "password": creds.password})
            # Wait for auth_result or first viewer arrival
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    payload = msg.json()
                    if payload.get("type") == "auth_result":
                        if not payload.get("success", False):
                            status_cb("Auth failed")
                            return
                        status_cb("Authenticated – waiting for viewer…")
                        break  # leave loop, proceed
            # Create PeerConnection when viewer message arrives
            pc = RTCPeerConnection(ICE_CONFIG)                    

            # Add tracks
            video_track = ScreenTrack(capture_fps=settings.fps, send_fps=settings.fps, scale=settings.scale)
            pc.addTrack(video_track)

            # Force H.264 only preference immediately
            try:
                transceiver = next(t for t in pc.getTransceivers() if t.sender.track is video_track)
                caps = RTCRtpSender.getCapabilities("video")
                h264_codecs = [c for c in caps.codecs if "H264" in c.mimeType]
                if h264_codecs:
                    transceiver.setCodecPreferences(h264_codecs)
            except Exception as e:
                logger.debug(f"Could not force H.264: {e}")

            # # Show what codec the sender finally uses
            # def get_negotiated_codec_info(pc):
            #     """Extracts negotiated video codec information from SDP"""
            #     # Determine which SDP to use (local or remote)
            #     sdp_source = pc.localDescription if pc.localDescription else pc.remoteDescription
            #     if not sdp_source:
            #         return None
                
            #     sdp = sdp_source.sdp
            #     in_video_section = False
            #     payload_types = []
            #     codec_info = {}
                
            #     for line in sdp.split('\n'):
            #         line = line.strip()
                    
            #         # Video section detection
            #         if line.startswith('m=video'):
            #             in_video_section = True
            #             # Extract payload types from m-line
            #             parts = line.split()
            #             if len(parts) >= 4:
            #                 payload_types = parts[3:]
            #             continue
                    
            #         # End of video section
            #         if line.startswith('m=') and in_video_section:
            #             break
                        
            #         # Process video section
            #         if in_video_section:
            #             # Extract codec mapping
            #             if line.startswith('a=rtpmap:'):
            #                 parts = line.split(':', 1)
            #                 if len(parts) < 2:
            #                     continue
            #                 payload, mapping = parts[1].split(' ', 1)
            #                 if payload in payload_types and not mapping.startswith('rtx/'):
            #                     # Found a non-RTX codec mapping
            #                     codec_name, clock_rate = mapping.split('/')[:2]
            #                     codec_info = {
            #                         'name': codec_name,
            #                         'payload_type': payload,
            #                         'clock_rate': clock_rate
            #                     }
            #                     # Return the first non-RTX codec found
            #                     return codec_info
                
            #     return None

            # # Usage in your debug function
            # async def _debug_codec():
            #     await asyncio.sleep(1)  # Give time for negotiation to complete
                
            #     # Get codec info from SDP
            #     codec_info = get_negotiated_codec_info(pc)
                
            #     if codec_info:
            #         print("Negotiated codec (from SDP):", codec_info['name'])
            #         print("Payload type:", codec_info['payload_type'])
            #         print("Clock rate:", codec_info['clock_rate'])
            #     else:
            #         print("No codec information available in SDP")

            # asyncio.ensure_future(_debug_codec())


            try:
                pc.addTrack(SystemAudioTrack())
            except Exception as e:
                logger.warning(f"Audio capture unavailable: {e}")

            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    await ws.send_json({
                        "type": "ice",
                        "candidate": candidate.sdp,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    })

            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            await ws.send_json({"type": "offer", "sdp": pc.localDescription.sdp, "id": creds.conn_id})
            status_cb("Offer sent – awaiting answer…")

            async def _log_final_codec(sender: RTCRtpSender):
                # wait until the private encoder appears (frame 0 encoded)
                while getattr(sender, "_RTCRtpSender__encoder", None) is None:
                    await asyncio.sleep(0.05)

                enc = sender._RTCRtpSender__encoder          # private, but works
                if hasattr(enc, 'codec_context'):
                    ctx = enc.codec_context
                    print(f"Final encoder: {ctx.name}  ({ctx.width}x{ctx.height}, br={ctx.bit_rate})")
                else:
                    print(f"Final encoder: {enc.__class__.__name__}")

            @pc.on("connectionstatechange")
            async def on_conn_state():
                if pc.connectionState == "connected":
                    try:
                        # sender = next(s for s in pc.getSenders()
                        #             if s.track and s.track.kind == "video")

                        # if hasattr(sender, "_RTCRtpSender__encoder") and sender._RTCRtpSender__encoder:
                        #     codec_ctx = sender._RTCRtpSender__encoder.codec_context
                        #     print("Negotiated codec:", codec_ctx.name)          # e.g. h264_nvenc
                        # else:
                        #     print("Encoder not ready yet")

                        vsender = next(s for s in pc.getSenders()
                           if s.track and s.track.kind == "video")
                        asyncio.create_task(_log_final_codec(vsender))
                            

                        # Only call when the underlying transport is ready
                        # if sender._ssrc:                 # ssrc set → encoder exists
                        #     params = sender.getParameters()
                        #     if params.codecs:
                        #         codec = params.codecs[0]
                        #         print(f"Negotiated codec: {codec.mimeType} "
                        #             f"(pt={codec.payloadType}, clock={codec.clockRate})")
                        #     else:
                        #         print("Codec list empty")
                        # else:
                        #     print("Sender not ready yet – skipping debug print")
                    except StopIteration:
                        print("No video sender found")


                    status_cb("Streaming – viewer connected")
                elif pc.connectionState in ("failed", "closed", "disconnected"):
                    status_cb("Disconnected")

            # Listen for signalling messages
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = msg.json()
                if data.get("type") == "answer":
                    answer = RTCSessionDescription(sdp=data["sdp"], type="answer")
                    await pc.setRemoteDescription(answer)
                    status_cb("Connected – streaming")
                elif data.get("type") == "ice":
                    candidate = data.get("candidate")
                    if candidate:
                        await pc.addIceCandidate(
                            {
                                "candidate": candidate,
                                "sdpMid": data.get("sdpMid"),
                                "sdpMLineIndex": data.get("sdpMLineIndex"),
                            }
                        )

async def run_client(settings: Settings, creds: Credentials, status_cb: Callable[[str], None]):
    """Run as WebRTC client (viewer)."""
    async with ClientSession() as session:
        async with session.ws_connect(settings.signalling_url) as ws:
            # Auth
            await ws.send_json({"type": "auth", "id": creds.conn_id, "password": creds.password})
            # Inform host that this viewer has authenticated and is waiting
            await ws.send_json({"type": "viewer_waiting", "id": creds.conn_id})

            pc = RTCPeerConnection(ICE_CONFIG)

            # Handle incoming tracks
            @pc.on("track")
            def on_track(track):
                print("on_track", track)
                if track.kind == "video":
                    status_cb("Receiving video…")

                    async def consume():
                        #  frame_count = 0
                        frame_count = 1
                        last_ts = time.time()
                        try:
                            while True:
                                try:
                                    frame = await track.recv()
                                except MediaStreamError:
                                    break  # stream ended
                                img = frame.to_ndarray(format="bgr24")
                                frame_count += 1
                                now = time.time()
                                if now - last_ts >= 1.0:
                                    fps = frame_count / (now - last_ts)
                                    cpu_raw = _process.cpu_percent(interval=None)
                                    cpu = cpu_raw / psutil.cpu_count(logical=True)
                                    gpu = None
                                    if _gputil:
                                        gpus = _gputil.getGPUs()
                                        if gpus:
                                            gpu = gpus[0].load * 100  # load is 0..1
                                    last_ts = now
                                    # frame_count = 0
                                    frame_count = 1
                                # Draw overlay when metrics are available
                                if 'fps' in locals():
                                    overlay = f"{fps:.1f} FPS  CPU:{cpu:.1f}%"
                                    # if gpu is not None:
                                    #     overlay += f"  GPU:{gpu:.0f}%"
                                    cv2.putText(img, overlay, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1,
                                                (0, 255, 0), 2)
                                cv2.imshow("ScreenShare", img)
                                if cv2.waitKey(1) & 0xFF == ord("q"):
                                    break
                        finally:
                            await pc.close()
                            cv2.destroyAllWindows()
                            status_cb("Viewer closed")

                    asyncio.ensure_future(consume())
                elif track.kind == "audio":
                    status_cb("Receiving audio…")
                    player_p = pyaudio.PyAudio()
                    out_stream = player_p.open(
                        format=AUDIO_FORMAT,
                        channels=AUDIO_CHANNELS,
                        rate=AUDIO_RATE,
                        output=True,
                    )

                    async def play_audio():
                        try:
                            while True:
                                try:
                                    frame = await track.recv()
                                except MediaStreamError:
                                    break
                            # out_stream.write(frame.planes[0].to_bytes())
                            out_stream.write(frame.to_ndarray().tobytes())
                        finally:
                            out_stream.stop_stream()
                            out_stream.close()
                            player_p.terminate()
                    asyncio.ensure_future(play_audio())

            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    await ws.send_json({
                        "type": "ice",
                        "candidate": candidate.sdp,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    })

            # Listen for signalling messages
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = msg.json()
                if data.get("type") == "offer":
                    offer = RTCSessionDescription(sdp=data["sdp"], type="offer")
                    await pc.setRemoteDescription(offer)
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)
                    await ws.send_json({"type": "answer", "sdp": pc.localDescription.sdp})
                    status_cb("Answer sent – waiting for ICE…")
                elif data.get("type") == "ice":
                    candidate = data.get("candidate")
                    if candidate:
                        await pc.addIceCandidate(
                            {
                                "candidate": candidate,
                                "sdpMid": data.get("sdpMid"),
                                "sdpMLineIndex": data.get("sdpMLineIndex"),
                            }
                        )

# ---------------------------------------------------------------------------
# Utils

def get_local_ips() -> list[str]:
    """Return IPv4 addresses that are likely reachable by peers.

    Criteria:
    • Exclude loopback (127.*) and link-local (169.254.*) addresses.
    • Exclude addresses whose reverse-DNS lookup fails (heuristic for
      disconnected / virtual adapters).
    """
    def _valid(ip: str) -> bool:
        if ip.startswith(("127.", "169.254.")):
            return False
        try:
            socket.gethostbyaddr(ip)
        except socket.herror:
            # No reverse DNS: treat as unreachable as per user request
            return False
        return True

    ips = set()
    # Method 1: hostname lookup
    for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = res[4][0]
        if _valid(ip):
            ips.add(ip)
    # Method 2: default route trick (captures primary adapter reliably)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if _valid(ip):
            ips.add(ip)
    except Exception:
        pass
    return sorted(ips)

# ---------------------------------------------------------------------------
# Tkinter GUI
class HubGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("WebRTC Screen Share Hub")
        # ---------------- Styling ----------------
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")  # more modern than default
        except tk.TclError:
            pass
        bg = "#ffffff"       # light background
        fg = "#000000"
        accent = "#0078d4"    # blue accent similar to Fluent design
        self.root.configure(bg=bg)
        self.style.configure(".", background=bg, foreground=fg, fieldbackground=bg)
        self.style.configure("TLabel", background=bg, foreground=fg)
        self.style.configure("TRadiobutton", background=bg, foreground=fg)
        self.style.configure("TScale", background=bg)
        self.style.configure("Accent.TButton", background=accent, foreground="#ffffff", padding=6, font=("Segoe UI", 10, "bold"))
        self.style.map("Accent.TButton",
                       background=[("active", "#006cbe"), ("disabled", "#d0d0d0")],
                       foreground=[("disabled", "#888888")])
        self.root.resizable(False, False)
        self.status_var = tk.StringVar(value="Idle")
        # Session management
        self.session_loop: Optional[asyncio.AbstractEventLoop] = None
        self.session_thread: Optional[threading.Thread] = None
        self.session_kind: Optional[str] = None  # 'host' or 'client'

        # ---------- Widgets ------------
        pad = 8
        frm = ttk.Frame(self.root, padding=pad)
        frm.grid(row=0, column=0)

        # Mode toggle
        self.mode_var = tk.StringVar(value="host")
        ttk.Label(frm, text="Mode:").grid(row=0, column=0, sticky="e", pady=pad)
        rb_host = ttk.Radiobutton(frm, text="Host", variable=self.mode_var, value="host", command=self.update_mode)
        rb_client = ttk.Radiobutton(frm, text="Client", variable=self.mode_var, value="client", command=self.update_mode)
        rb_host.grid(row=0, column=1, sticky="w")
        rb_client.grid(row=0, column=2, sticky="w")

        ttk.Label(frm, text="Connection ID:").grid(row=1, column=0, sticky="e", pady=pad)
        self.id_var = tk.StringVar(value=f"{random.randint(0, 999999):06d}")
        ttk.Entry(frm, textvariable=self.id_var, width=10, justify="center").grid(row=1, column=1, pady=pad)

        ttk.Label(frm, text="Password:").grid(row=2, column=0, sticky="e", pady=pad)
        self.pw_var = tk.StringVar(value=f"{random.randint(0, 9999):04d}")
        ttk.Entry(frm, textvariable=self.pw_var, width=10, justify="center").grid(row=2, column=1, pady=pad)

        ttk.Label(frm, text="Signalling URL:").grid(row=3, column=0, sticky="e", pady=pad)
        # Pick first non-loopback, non-link-local IP else fallback to localhost 3.7.121.59:8765
        # primary_ip = next((ip for ip in get_local_ips() if not ip.startswith(("127.", "169.254."))), "localhost")
        # self.sig_var = tk.StringVar(value=f"ws://{primary_ip}:8765/ws")
        self.sig_var = tk.StringVar(value=f"ws://3.7.121.59:8765/ws")
        ttk.Entry(frm, textvariable=self.sig_var, width=28).grid(row=3, column=1, columnspan=2, pady=pad)
        # Display parsed server host/IP
        self.server_info_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.server_info_var, foreground="gray").grid(row=3, column=3, sticky="w")
        # Update whenever URL changes
        self.sig_var.trace_add('write', lambda *a: self._update_server_label())

        # Fixed FPS (slider removed as per user request)
        self.fps_var = tk.IntVar(value=30)  # always 30 fps sent
        # ttk.Label(frm, text="FPS:").grid(row=4, column=0, sticky="e", pady=pad)
        # ttk.Label(frm, text="30 (fixed)").grid(row=4, column=1, columnspan=3, sticky="w")

        # Scale slider
        ttk.Label(frm, text="Scale:").grid(row=5, column=0, sticky="e", pady=pad)
        self.scale_var = tk.DoubleVar(value=0.5)
        self.scale_slider = ttk.Scale(frm, from_=0.2, to=1.0, orient="horizontal", variable=self.scale_var)
        self.scale_slider.grid(row=5, column=1, columnspan=2, pady=pad, sticky="we")
        # Current Scale label (formatted to two decimals)
        self.scale_text = tk.StringVar(value=f"{self.scale_var.get():.2f}")
        self.scale_value_label = ttk.Label(frm, textvariable=self.scale_text, width=4)
        self.scale_value_label.grid(row=5, column=3, padx=(0, pad))
        # Update scale label whenever slider moves
        self.scale_var.trace_add('write', lambda *a: self.scale_text.set(f"{self.scale_var.get():.2f}"))

        # Initialize server label once
        self._update_server_label()

        # Buttons
        self.btn_host = ttk.Button(frm, text="Share Screen", style="Accent.TButton", command=self.start_host)
        self.btn_host.grid(row=6, column=0, pady=pad, sticky="we")
        self.btn_client = ttk.Button(frm, text="Connect", style="Accent.TButton", command=self.start_client)
        self.btn_client.grid(row=6, column=1, columnspan=2, pady=pad, sticky="we")

        # Info label
        ttk.Label(frm, text="First connect by host's credentials then Share Screen in the host PC.", foreground="gray").grid(row=8, column=0, columnspan=3, pady=(0, pad))

        # Status label
        ttk.Label(frm, textvariable=self.status_var, foreground="blue").grid(row=9, column=0, columnspan=3, pady=(pad, 0))
        # Status indicator (red/orange/green)
        self.indicator_canvas = tk.Canvas(frm, width=16, height=16, highlightthickness=0, bg=bg)
        self.indicator_canvas.grid(row=9, column=3, padx=(pad, 0))
        self.indicator = self.indicator_canvas.create_oval(2, 2, 14, 14, fill="red", outline="")

        # Spinner (indeterminate progress)
        self.spinner = ttk.Progressbar(frm, mode="indeterminate", length=120)
        self.spinner.grid(row=10, column=0, columnspan=3, pady=(0, pad))
        self.spinner.grid_remove()

        # Local IP display
        ip_list = ", ".join(get_local_ips()) or "N/A"
        ttk.Label(frm, text=f"Your IP(s): {ip_list}", foreground="gray").grid(row=11, column=0, columnspan=3, pady=(0, pad))

        # Public IP display
        public_ip = get_public_ip()
        ttk.Label(frm, text=f"Public IP: {public_ip or 'Unavailable'}", foreground="gray").grid(row=12, column=0, columnspan=3, pady=(0, pad))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Set initial button states
        self.update_mode()

    def update_mode(self):
        """Enable/disable buttons based on selected mode."""
        mode = self.mode_var.get()
        if mode == "host":
            self.btn_host.state(["!disabled"])
            self.btn_client.state(["disabled"])
            # Auto-fill credentials if blank when switching back to Host mode
            if not self.id_var.get():
                self.id_var.set(f"{random.randint(0, 999999):06d}")
            if not self.pw_var.get():
                self.pw_var.set(f"{random.randint(0, 9999):04d}")
        else:
            self.btn_host.state(["disabled"])
            self.btn_client.state(["!disabled"])
            # Blank credentials for Client mode
            self.id_var.set("")
            self.pw_var.set("")
        # Start/stop passive listener depending on mode
        if mode == "host":
            self._start_listener()
        else:
            self._stop_listener()
        # Enable/disable sliders
        if mode == "host":
            self.scale_slider.state(["!disabled"])
        else:
            self.scale_slider.state(["disabled"])

    def stop(self):
        super().stop()
        if getattr(self, "use_dxcam", False):
            try:
                self.camera.stop()
            except Exception:
                pass

    # ---------------- Internal helpers ----------------
    def _update_server_label(self):
        """Parse the signalling URL and show the server host/IP."""
        import socket, urllib.parse
        url = self.sig_var.get().strip()
        if not url:
            self.server_info_var.set("")
            return
        if not url.startswith("ws://") and not url.startswith("wss://"):
            url = "ws://" + url  # allow host:port shorthand
        try:
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or "?"
            try:
                ip = socket.gethostbyname(host)
            except Exception:
                ip = host
            self.server_info_var.set(f"Server IP: {ip}")
        except Exception:
            self.server_info_var.set("Server IP: ?")

    def _async_thread(self, coro):
        """Run *coro* in its own event loop thread; allows external loop.stop()."""
        def _run(loop, co):
            asyncio.set_event_loop(loop)
            task = loop.create_task(co)
            # Stop the loop automatically once the main task completes
            task.add_done_callback(lambda _t: loop.call_soon_threadsafe(loop.stop))
            try:
                loop.run_forever()
            finally:
                # Ensure task is cancelled/awaited before closing loop
                try:
                    task.cancel()
                    loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
                except Exception:
                    pass
                loop.close()
        thread_loop = asyncio.new_event_loop()
        t = threading.Thread(target=_run, args=(thread_loop, coro), daemon=True)
        t.start()
        return thread_loop, t

    def _status(self, msg: str):
        logger.info(msg)
        self.status_var.set(msg)
        self._update_indicator(msg)

    def _start_listener(self):
        """Start a lightweight websocket listener to detect waiting viewers."""
        if self.session_kind is not None or getattr(self, "listener_loop", None):
            return  # already running a session or listener
        creds = Credentials(self.id_var.get().strip(), self.pw_var.get().strip())
        settings = Settings(self.sig_var.get().strip(), self.fps_var.get(), self.scale_var.get())

        async def _listener():
            try:
                async with ClientSession() as session:
                    async with session.ws_connect(settings.signalling_url) as ws:
                        await ws.send_json({"type": "auth", "id": creds.conn_id, "password": creds.password})
                        async for msg in ws:
                            if msg.type != WSMsgType.TEXT:
                                continue
                            data = msg.json()
                            if data.get("type") == "viewer_waiting" and data.get("id") == creds.conn_id:
                                self._status("Viewer ready – click Share Screen")
                                break
            except Exception as e:
                logger.debug("Listener stopped: %s", e)

        self.listener_loop, self.listener_thread = self._async_thread(_listener())

    def _stop_listener(self):
        loop = getattr(self, "listener_loop", None)
        if loop:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        self.listener_loop = None
        self.listener_thread = None

    def _update_indicator(self, msg: str):
        msg_l = msg.lower()
        if any(w in msg_l for w in ("starting", "connecting", "waiting")):
            # connecting
            self.indicator_canvas.itemconfig(self.indicator, fill="orange")
            self.spinner.grid()
            self.spinner.start(10)
        elif any(w in msg_l for w in ("receiving", "ice completed", "viewer connected", "streaming", "connected")):
            # connected
            self.indicator_canvas.itemconfig(self.indicator, fill="green")
            self.spinner.stop()
            self.spinner.grid_remove()
        elif "viewer ready" in msg_l:
            # viewer authenticated and waiting
            self.indicator_canvas.itemconfig(self.indicator, fill="orange")
            self.spinner.stop()
            self.spinner.grid_remove()
        else:
            # idle / error
            self.indicator_canvas.itemconfig(self.indicator, fill="red")
            self.spinner.stop()
            self.spinner.grid_remove()

    # ---------------- Button handlers ----------------
    def _start_session(self, coro, kind: str):
        if self.session_loop is not None:
            return  # Already running
        self.session_kind = kind
        self.session_loop, self.session_thread = self._async_thread(coro)
        # Toggle buttons
        if kind == "host":
            self.btn_host.config(text="Disconnect", command=self.stop_session)
            self.btn_client.state(["disabled"])
            self._stop_listener()
        else:
            self.btn_client.config(text="Disconnect", command=self.stop_session)
            self.btn_host.state(["disabled"])

    def stop_session(self):
        if self.session_loop:
            loop = self.session_loop
            def _shutdown():
                # Cancel all tasks to avoid "destroyed but pending" warnings
                for task in asyncio.all_tasks(loop):
                    task.cancel()
                loop.stop()
            try:
                loop.call_soon_threadsafe(_shutdown)
            except Exception:
                pass
        self.session_loop = None
        self.session_thread = None
        self.session_kind = None
        # Reset buttons and status
        self.btn_host.config(text="Share Screen", command=self.start_host)
        self.btn_client.config(text="Connect", command=self.start_client)
        self.update_mode()
        self._status("Idle")

    def start_host(self):
        creds = Credentials(self.id_var.get().strip(), self.pw_var.get().strip())
        settings = Settings(self.sig_var.get().strip(), self.fps_var.get(), self.scale_var.get())
        self._status("Starting host…")
        self._start_session(run_host(settings, creds, self._status), "host")

    def start_client(self):
        creds = Credentials(self.id_var.get().strip(), self.pw_var.get().strip())
        settings = Settings(self.sig_var.get().strip(), self.fps_var.get(), self.scale_var.get())
        self._status("Connecting to host…")
        self._start_session(run_client(settings, creds, self._status), "client")

    # ---------------- Cleanup ----------------
    def on_close(self):
        self.root.quit()

    def run(self):
        self.root.mainloop()

# ---------------------------------------------------------------------------

def main():
    HubGUI().run()

if __name__ == "__main__":
    main()
