#!/usr/bin/env python3
"""
Minimal WebSocket-based signalling server for WebRTC
---------------------------------------------------
This server simply relays JSON messages between connected peers.
All logic (authentication, offer/answer, ICE) is handled by the
end-points.  Every text message received from one websocket connection
is broadcast verbatim to every *other* connection.

Running:
    python signalling_server.py --host 0.0.0.0 --port 8765

    # elevated PowerShell
    Start-Process python -Verb RunAs -ArgumentList "signalling_server.py --host 0.0.0.0 --port 8765"

One public TCP port is enough because the heavy lifting (media) happens
peer-to-peer through STUN/TURN.
"""
import argparse
import asyncio
import json
import logging
from aiohttp import web
from typing import Set
import subprocess
import ctypes
import platform
import socket
from contextlib import suppress
from security import safe_command

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

CLIENTS: Set[web.WebSocketResponse] = set()


def ensure_firewall_rule(port: int = 8765, name: str = "WebRTC Signalling 8765"):
    """Add inbound TCP allow rule for the signalling port on Windows."""
    # Best-effort: skip silently if anything fails
    logging.info("Checking firewall rule for port %s", port)
    logging.info("Platform: %s", platform.system())
    if platform.system() != "Windows":
        return  # Not Windows, nothing to do

    # Check if rule exists already
    check_cmd = [
        "powershell", "-Command",
        f"if (Get-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue) {{exit 0}} else {{exit 1}}"
    ]
    try:
        if safe_command.run(subprocess.call, check_cmd) == 0:
            return  # Rule already present
    except FileNotFoundError:
        # PowerShell missing (very rare) – give up silently
        return

    # Require elevation
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            print(f"[Firewall] Admin privileges required to open TCP {port}. Run as Administrator.")
            return
    except Exception:
        # ctypes may fail in some environments; skip
        return

    add_cmd = [
        "powershell", "-Command",
        (
            f"New-NetFirewallRule -DisplayName '{name}' "
            f"-Direction Inbound -Action Allow -Protocol TCP -LocalPort {port} -Profile Any"
        )
    ]
    try:
        subprocess.check_call(add_cmd)
        print(f"[Firewall] Added inbound rule '{name}' (TCP {port}).")
    except Exception as exc:
        print(f"[Firewall] Unable to add rule: {exc}")


def ensure_port_mapping(port: int = 8765, description: str = "WebRTC Signalling"):
    """Attempt to add an automatic router port mapping (UPnP ➔ NAT-PMP ➔ PCP)."""
    # Best-effort: skip silently if anything fails
    try:
        import miniupnpc
    except ImportError:
        print("[UPnP] miniupnpc not installed; skipping automatic port mapping.")
        return

    try:
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        ndev = upnp.discover()
        if ndev == 0:
            print("[UPnP] No IGD discovered.")
            return
        upnp.selectigd()
        external_ip = upnp.externalipaddress()
        existing = upnp.getspecificportmapping(port, 'TCP')
        if existing:
            print(f"[UPnP] Port {port} already mapped on router (external {external_ip}).")
            return
        upnp.addportmapping(port, 'TCP', upnp.lanaddr, port, description, '')
        print(f"[UPnP] Added port mapping {external_ip}:{port} -> {upnp.lanaddr}:{port} (TCP).")
    except Exception as exc:
        print(f"[UPnP] Failed to add port mapping: {exc}")

    # ---------- NAT-PMP fallback via pynatpmp ----------
    # First try miniupnpc NATPMP if available
    try:
        import miniupnpc
        if hasattr(miniupnpc, 'NATPMP'):
            pmp = miniupnpc.NATPMP()
            ext_ip_pmp = pmp.externalipaddress()[1]
            res = pmp.addportmapping(port, port, 'TCP', 0)
            if res[0] == 0:
                print(f"[NAT-PMP] Added port mapping {ext_ip_pmp}:{port} -> <router>:{port} (TCP).")
                return
    except Exception:
        pass

    # Fallback to pynatpmp which works on Windows
    try:
        from natpmp import NatPMP, NATPMPProtocolError
        client = NatPMP()
        response = client.get_external_address()
        ext_ip_pmp = '.'.join(map(str, response.external_address))
        try:
            client.add_port_mapping(protocol='tcp', private_port=port, public_port=port, lifetime=0)
            print(f"[NAT-PMP] Added port mapping {ext_ip_pmp}:{port} -> <router>:{port} (TCP) via pynatpmp.")
            return
        except NATPMPProtocolError as e:
            print(f"[NAT-PMP] pynatpmp error: {e}")
    except ImportError:
        print("[NAT-PMP] py-natpmp not installed; skipping.")
    except Exception as exc2:
        print(f"[NAT-PMP] Failed: {exc2}")

    # ---------- PCP fallback ----------
    try:
        import miniupnpc
        # miniupnpc can use PCP via the UPnP object when usepcp flag is set
        pcp = miniupnpc.UPnP()
        pcp.usepcp = True
        if pcp.discover() == 0:
            raise RuntimeError("No PCP-capable gateway discovered.")
        pcp.selectigd()
        ext_ip_pcp = pcp.externalipaddress()
        pcp.addportmapping(port, 'TCP', pcp.lanaddr, port, description, '')
        print(f"[PCP] Added port mapping {ext_ip_pcp}:{port} -> {pcp.lanaddr}:{port} (TCP).")
        return
    except Exception as exc3:
        print(f"[PCP] Failed: {exc3}")



async def websocket_handler(request: web.Request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    CLIENTS.add(ws)
    peer = request.remote
    logging.info("WebSocket connected: %s", peer)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                # Expect JSON; forward as-is
                try:
                    payload = json.loads(msg.data)
                except ValueError:
                    logging.warning("Non-JSON message from %s ignored", peer)
                    continue

                # Handle optional auth handshake convenience
                if payload.get("type") == "auth":
                    # echo a success message to authenticate immediately
                    await ws.send_str(json.dumps({"type": "auth_result", "success": True}))
                    # But still broadcast the auth payload to others if they care
                
                # Broadcast to all other peers
                for client in list(CLIENTS):
                    if client is not ws and not client.closed:
                        await client.send_str(msg.data)
            elif msg.type == web.WSMsgType.ERROR:
                logging.error("WebSocket error from %s: %s", peer, ws.exception())
                break
    finally:
        CLIENTS.discard(ws)
        logging.info("WebSocket disconnected: %s", peer)
    return ws


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/ws", websocket_handler)
    return app


def main():
    parser = argparse.ArgumentParser(description="Minimal signalling server for WebRTC")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    args = parser.parse_args()

    app = create_app()
    ensure_firewall_rule(args.port)
    ensure_port_mapping(args.port)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
