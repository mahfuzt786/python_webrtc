# WebRTC Screen Share Hub

A lightweight, **one-script** solution for live screen-sharing **with system audio** over your local network or the internet.

*   No browser extensions.
*   No router configuration (built-in STUN/TURN servers for NAT traversal).
*   Simple Tkinter GUI – suitable for absolute beginners.

---

## 1. Prerequisites

| Requirement | Tested Version |
|-------------|----------------|
| Windows 10/11 64-bit | – |
| Python | 3.10 – 3.12 |
| Visual C++ Build Tools | _(only if `pip` needs to compile PyAudio)_ |

> 💡 **TIP** – If you are unfamiliar with Python, install it from https://python.org and be sure to tick **“Add python.exe to PATH”** during setup.

---

## 2. Download the project

```powershell
# Any folder you like
git clone <this-repository-url>
cd webRTC
```
If you received the project as a .zip, simply extract it and open *PowerShell* or *CMD* inside the extracted `webRTC` folder.

---

## 3. Set up a virtual environment  (optional but recommended)

```powershell
python -m venv webenv
# Activate it (PowerShell)
webenv\Scripts\Activate.ps1
```
Your prompt should now start with **(webenv)**.

---

## 4. Install dependencies

The process can take a few minutes – it pulls packages such as **OpenCV**, **aiortc**, **PyAudio**, etc.

> **Linux extra packages**  
> *Before* running `pip`, install your distro’s Tk + PortAudio bindings so that `tkinter` and `pyaudio` imports succeed:
>
> ```bash
> # Debian / Ubuntu
> sudo apt update && sudo apt install python3-tk python3-pyaudio
>
> # Fedora
> sudo dnf install python3-tkinter python3-pyaudio
>
> # Arch
> sudo pacman -S tk pyaudio
> ```
>
> **macOS Homebrew users**  
> If the PyAudio wheel fails, install the PortAudio library first:
>
> ```bash
> brew install portaudio
> pip install pyaudio
> ```
>
> On Windows everything is included in the official Python installer.


If PyAudio fails, install the matching pre-built wheel from https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio and retry.

---

### Windows
```powershell
pip install -r requirements.txt
```

### Linux
```bash
pip install -r requirements_linux.txt
```

### macOS
```bash
pip install -r requirements_mac.txt
```

## 5. Run the signalling server **with Docker (alternative)**

If you don’t want to install Python on the host machine you can containerise just the signalling server.  The GUI scripts still run on your desktop.

```powershell
# Build image (only once)
docker build -t webrtc-hub .

# Or use the provided compose file
docker compose up -d  # builds & starts container named 'webrtc_signalling'
```
The container exposes port **8765**; make sure it is open in your firewall / router.

Continue with step 6 to run the Host GUI.

---

## 6. Start the signalling server (native, no Docker)

A tiny WebSocket server coordinates the connection – run it **once** on **any** computer in the network (usually the Host PC).

```powershell
python signalling_server.py --host 0.0.0.0 --port 8765
```
You should see:
```
======== Running on http://0.0.0.0:8765 ========
(Press CTRL+C to quit)
```
Leave this window open.

---

## 7. Launch the Host (screen-sharing) GUI

```powershell
python webrtc_gui.py
```
1. _Mode_ defaults to **Host**.
2. The app auto-generates a **Connection ID** (6 digits) and **Password** (4 digits). You can change them if you like.
3. Click **Share Screen** **after** you see “**Viewer ready – click Share Screen**”.  
   (This message appears when a client has connected with the correct credentials.)

---

## 8. Launch the Client (viewer) GUI – on the same or another PC

On the viewing PC repeat the installation steps (Python → `pip install -r requirements.txt`).  Then run:

```powershell
python webrtc_gui.py
```
1. Change _Mode_ to **Client** → the **ID** and **Password** boxes become empty.
2. Enter the **Connection ID** and **Password** shown on the Host.
3. Ensure the **Signalling URL** points to the Host’s IP, e.g.:
   *Local network example*
   ```
   ws://192.168.1.9:8765/ws
   ```
4. Click **Connect**.  The Host will be notified, and once it clicks **Share Screen** you should see the screen and hear audio.

Press **Q** in the video window to close the viewer.

---

## 9. Using it over the Internet

1. The code already includes Google STUN and Viagenie TURN servers, which handle most NAT/firewall cases automatically.
2. Make sure **port 8765** on the Host (signalling server) is reachable.  This can be via:
   * Port-forwarding on your router **or**
   * Running the signalling server on a VPS/cloud host with a public IP.
3. Give the Client a public URL such as:
   ```
   ws://your-public-ip:8765/ws
   ```

If connectivity still fails, try a commercial TURN service and add its credentials in `webrtc_gui.py` (see the `ICE_CONFIG` list).

---

## 10. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| **Black video when sharing DRM-protected apps (Netflix, etc.)** | Not a bug. Windows blocks capture of protected content.  Future upgrade will use Windows Graphics Capture API (see *TODO*). |
| `WinError 10049 Could not bind to 169.254…` lines | Harmless – aiortc probes all local interfaces. |
| `AttributeError: 'AudioPlane' object has no attribute 'to_bytes'` | Upgrade `av` (`pip install --upgrade av`) *or* switch to an earlier Python version (3.10/3.11). |
| Audio stutters | Lower FPS or Scale on the Host. |

---

## 11. Keyboard / GUI reference

| Element | Purpose |
|---------|---------|
| **Mode** radio | Choose Host (share) or Client (view). |
| **FPS / Scale** sliders | Only active in Host mode – trade-off quality vs. CPU/network. |
| **Indicator (red/orange/green)** | Idle → Waiting/Connecting → Streaming. |
| **Disconnect** | Appears after a session starts to end it gracefully. |
| **Q key** in viewer window | Close viewer and end call. |

---

## 12. Uninstallation

Simply delete the project folder and (optionally) the virtual-env directory.

---

## 13. License & Credits

MIT License.  Built with:
* **aiortc** – Python WebRTC implementation
* **OpenCV / PyAutoGUI** – screen capture
* **PyAudio** – system-audio capture

Enjoy secure, peer-to-peer screen-sharing!  
_Questions / suggestions welcome._
