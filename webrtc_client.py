#!/usr/bin/env python3
"""
webrtc_client.py
----------------
Connects to the signalling server, authenticates, receives a WebRTC
video track from the host, and displays the frames using OpenCV.
"""
import argparse
import asyncio
import json
import logging

import cv2
from aiohttp import ClientSession, WSMsgType
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")


async def run(args):
    async with ClientSession() as session:
        async with session.ws_connect(args.signalling) as ws:
            logging.info("Connected to signalling server %s", args.signalling)
            # Send auth message
            await ws.send_str(json.dumps({"type": "auth", "id": args.id, "password": args.password, "role": "client"}))

            pc = RTCPeerConnection()
            recorder = MediaBlackhole()  # swallows incoming audio

            # Display incoming video
            @pc.on("track")
            async def on_track(track):
                logging.info("Track received: %s", track.kind)
                if track.kind == "video":
                    while True:
                        frame = await track.recv()
                        img = frame.to_ndarray(format="bgr24")
                        cv2.imshow("ScreenShare", img)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            break
                    await track.stop()
                    cv2.destroyAllWindows()
                elif track.kind == "audio":
                    recorder.addTrack(track)

            # Send our ICE candidates to host
            @pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    await ws.send_str(json.dumps({"type": "candidate", "candidate": candidate.sdp, "sdpMid": candidate.sdpMid, "sdpMLineIndex": candidate.sdpMLineIndex}))

            # Main signalling loop
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "offer":
                        offer_desc = RTCSessionDescription(sdp=data["sdp"], type=data["sdpType"])
                        await pc.setRemoteDescription(offer_desc)
                        answer = await pc.createAnswer()
                        await pc.setLocalDescription(answer)
                        await ws.send_str(json.dumps({"type": "answer", "sdp": pc.localDescription.sdp, "sdpType": pc.localDescription.type}))
                        logging.info("Answer sent")
                    elif data.get("type") == "candidate":
                        if pc.remoteDescription is None:
                            continue
                        candidate = data.get("candidate")
                        if candidate:
                            await pc.addIceCandidate(candidate)
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
            await recorder.stop()
            await pc.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebRTC screen share client")
    parser.add_argument("--signalling", default="ws://localhost:8765/ws", help="WebSocket signalling server URL")
    parser.add_argument("--id", default="admin", help="Credential ID/user")
    parser.add_argument("--password", default="admin", help="Credential password")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
