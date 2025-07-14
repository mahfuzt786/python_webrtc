#!/usr/bin/env python3
"""
webrtc_host.py
--------------
Shares the host machine's screen (and optionally audio) to one or more
remote peers via WebRTC.  A minimal WebSocket signalling server
("signalling_server.py") must be running and reachable by both the host
and clients.

Example usage (default credentials: id=admin, pwd=admin):

    python webrtc_host.py --signalling ws://localhost:8765/ws --id admin --password admin

Remote client can then run `webrtc_client.py` and connect via the same
signalling URL.
"""
import argparse
import asyncio
import json
import logging
import os
from typing import Optional

import cv2
import numpy as np
import pyautogui
import fractions
from aiohttp import ClientSession, WSMsgType
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.signaling import BYE
import av
import pyaudio

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")


# Audio capture constants
AUDIO_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_FORMAT = pyaudio.paInt16


class ScreenTrack(VideoStreamTrack):
    """A video stream track that captures the local screen."""

    def __init__(self, fps: int = 30, scale: float = 0.5):
        super().__init__()  # don't forget this
        self.fps = fps
        self.scale = scale
        self.counter = 0

    async def recv(self):
        # regulate FPS sleep
        await asyncio.sleep(1 / self.fps)

        img = pyautogui.screenshot()
        frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGR2RGB)
        if self.scale != 1.0:
            frame = cv2.resize(frame, (0, 0), fx=self.scale, fy=self.scale)

        av_frame = av.VideoFrame.from_ndarray(frame, format="rgb24")
        av_frame.pts = self.counter
        av_frame.time_base = fractions.Fraction(1, self.fps)
        self.counter += 1
        return av_frame


class SystemAudioTrack(AudioStreamTrack):
    """Capture system audio via PyAudio and expose as AudioStreamTrack."""

    def __init__(self):
        super().__init__()
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=AUDIO_FORMAT,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            input=True,
            frames_per_buffer=1024,
        )

    async def recv(self):
        data = await asyncio.get_event_loop().run_in_executor(None, self.stream.read, 1024)
        samples = np.frombuffer(data, dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = AUDIO_RATE
        return frame


async def run(args):
    # WebSocket signalling
    async with ClientSession() as session:
        async with session.ws_connect(args.signalling) as ws:
            logging.info("Connected to signalling server %s", args.signalling)

            # Send auth
            auth_msg = {"type": "auth", "id": args.id, "password": args.password, "role": "host"}
            await ws.send_str(json.dumps(auth_msg))

            # Wait for auth_result (or bypass if server is open)
            while True:
                msg = await ws.receive()
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "auth_result":
                        if not data.get("success", False):
                            logging.error("Authentication failed")
                            return
                        logging.info("Authentication success")
                        break
                    else:
                        # ignore any other message until auth done
                        continue

            # Create peer connection
            config = RTCConfiguration(iceServers=[
                RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                RTCIceServer(urls=["turn:numb.viagenie.ca"], username="webrtc@live.com", credential="muazkh")
            ])
            pc = RTCPeerConnection(configuration=config)
            pcs = set()
            pcs.add(pc)

            # Add tracks
            pc.addTrack(ScreenTrack(fps=args.fps, scale=args.scale))

            # (Audio disabled until frame timestamping is implemented properly)

            # Send our ICE candidates to the peer via signalling
            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    await ws.send_str(json.dumps({"type": "candidate", "candidate": candidate.sdp, "sdpMid": candidate.sdpMid, "sdpMLineIndex": candidate.sdpMLineIndex}))

            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            await ws.send_str(json.dumps({"type": "offer", "sdp": pc.localDescription.sdp, "sdpType": pc.localDescription.type}))
            logging.info("Offer sent, waiting for answer…")

            # Handle signalling messages
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "answer":
                        answer_desc = RTCSessionDescription(sdp=data["sdp"], type=data["sdpType"])
                        await pc.setRemoteDescription(answer_desc)
                        logging.info("Remote description set; streaming started")
                    elif data.get("type") == "candidate":
                        if pc.remoteDescription is None:
                            continue  # wait until we have remote description
                        candidate = data.get("candidate")
                        if candidate:
                            await pc.addIceCandidate(candidate)
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break

            # Cleanup
            logging.info("Signalling finished / socket closed, shutting down")
            await pc.close()
            pcs.discard(pc)

            # Wait a moment for graceful shutdown
            await asyncio.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Screen sharing host over WebRTC")
    parser.add_argument("--signalling", default="ws://localhost:8765/ws", help="WebSocket signalling server URL")
    parser.add_argument("--id", default="admin", help="Credential ID/user")
    parser.add_argument("--password", default="admin", help="Credential password")
    parser.add_argument("--fps", type=int, default=15, help="Capture frames per second")
    parser.add_argument("--scale", type=float, default=0.5, help="Scale factor for captured frames (0-1)")

    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
