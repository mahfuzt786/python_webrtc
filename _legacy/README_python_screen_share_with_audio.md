# Python Screen Sharing with Audio Support

This solution provides cursor-free screen sharing between devices using Python with full audio support. Unlike browser-based WebRTC solutions, this implementation ensures that the mouse cursor is never visible in the shared screen by leveraging system-level screen capture, while also streaming system audio for a complete sharing experience.

## System Components

1. **Screen Share Server with Audio (`python_screen_share_server_with_audio.py`)**: 
   - Captures the screen without cursor 
   - Records system audio
   - Streams both to connected clients

2. **Screen Share Client with Audio (`python_screen_share_client_with_audio.py`)**:
   - Connects to the server
   - Displays the shared screen
   - Plays synchronized audio with volume control

## Key Features Added

- **System Audio Capture and Playback**: Streams audio from sender's system to receivers
- **Volume Control**: Adjust volume level on the client side
- **Mute/Unmute**: Toggle audio playback without disconnecting
- **Synchronized A/V**: Audio and video are transmitted separately but synchronized on playback
- **Bandwidth Optimization**: Configurable audio quality settings (sample rate, channels)

## Audio Technical Details

### Audio Pipeline

1. **Capture**: 
   - Uses PyAudio to capture system audio
   - Default: 22.05 kHz sample rate, mono channel, 16-bit PCM

2. **Transport**: 
   - Audio frames are sent as separate message type
   - Audio and video packets are interleaved but with distinct headers

3. **Playback**:
   - Client buffers audio in a queue to handle network jitter
   - Volume adjustment done in real-time via NumPy array manipulation

### Audio Quality Settings

Server supports multiple audio quality profiles:

- **Low**: 11.025 kHz, mono (approx. 22 KB/s)
- **Medium** (default): 22.05 kHz, mono (approx. 44 KB/s)
- **High**: 44.1 kHz, stereo (approx. 176 KB/s)

## Performance Considerations

### CPU Usage

- **Server-side**: 
  - Screen capture: Moderate CPU usage (10-30% of one core, depending on resolution)
  - JPEG compression: CPU-intensive operation (can use 30-60% of one core at high resolutions/quality)
  - Scaling operations: Additional 5-15% CPU overhead when using scaling factors below 1.0
  - **Audio capture**: Additional 5-10% CPU usage
  - Total expected usage: 45-80% of one CPU core at default settings

- **Client-side**:
  - JPEG decompression: 10-20% of one core
  - Frame rendering: 5-15% of one core
  - **Audio playback**: Additional 3-7% CPU usage
  - Total expected usage: 18-42% of one CPU core

### GPU Usage

- **Default configuration**: Minimal GPU usage as operations are CPU-bound
- **With OpenCV GPU acceleration** (requires separate build of OpenCV with CUDA):
  - Can offload scaling and compression to GPU, reducing CPU usage by 20-40%
  - Server requires NVIDIA GPU with CUDA support for acceleration

### Network Bandwidth

Total bandwidth includes both video and audio streams:

| Resolution | Scale | Video Quality | FPS | Audio Quality | Total Bandwidth |
|------------|-------|--------------|-----|--------------|----------------|
| 1920x1080  | 0.75  | 70           | 15  | Medium       | ~5.2 Mbps      |
| 1920x1080  | 0.50  | 50           | 10  | Low          | ~2.1 Mbps      |
| 1920x1080  | 1.00  | 90           | 30  | High         | ~20.8 Mbps     |

### Memory Usage

- **Server**: 150-300 MB RAM + ~20 MB for audio processing
- **Client**: 100-200 MB RAM + ~15 MB for audio buffering

## Compression and Encoding Details

### Video Compression

- **Algorithm**: DCT-based JPEG compression (ISO/IEC 10918)
- **Library**: Uses libjpeg via OpenCV's imencode/imdecode functions
- **Quality control**: Variable compression ratio via IMWRITE_JPEG_QUALITY parameter (0-100)
- **Process**: 
   1. RGB to YCbCr color space conversion
   2. Chroma subsampling (typically 4:2:0 in default mode)
   3. 8x8 block DCT transform
   4. Quantization (quality-dependent)
   5. Zigzag ordering and entropy coding (Huffman)

### Audio Compression

- **Algorithm**: Raw PCM audio (uncompressed)
- **Format**: 16-bit signed integer samples
- **Quality control**: Through sample rate and channel count adjustment
- **Future enhancement**: Could implement Opus/MP3 compression for lower bandwidth usage

## Architecture

```
                 +------------------+
                 |                  |
                 |  Screen Sharing  |
                 |  Server + Audio  |
                 |                  |
                 +------------------+
                   |              |
                   |              |
     +-------------+              +-------------+
     |                                          |
     | PyAutoGUI                    PyAudio     |
     | Screen Capture               Audio       |
     V                              Capture     V
+------------------+              +------------------+
|   Local System   |              |   System Audio   |
|    (No Cursor)   |              |                  |
+------------------+              +------------------+
     |                                          |
     |                                          |
     +-------------+              +-------------+
                   |              |
                   V              V
                 +------------------+
                 |                  |
                 |   TCP Socket     |
                 |   Connection     |
                 |                  |
                 +------------------+
                          |
                          |
                          V
                 +------------------+
                 |                  |
                 |  Screen Sharing  |
                 |  Client + Audio  |
                 |                  |
                 +------------------+
                   |              |
                   |              |
     +-------------+              +-------------+
     |                                          |
     | Tkinter                      PyAudio     |
     | Display                      Audio       |
     V                              Playback    V
+------------------+              +------------------+
|  Client Display  |              |  Client Speakers |
|     Window       |              |                  |
+------------------+              +------------------+
```

## Setup Requirements

### Server-side Requirements
```
pip install opencv-python numpy pyautogui pillow pyaudio
```

### Client-side Requirements
```
pip install opencv-python numpy pillow pyaudio
```

## Usage Instructions

### Starting the Server

1. Run the server on the machine that wants to share its screen and audio:
```
python python_screen_share_server_with_audio.py
```

2. Optional parameters:
```
python python_screen_share_server_with_audio.py --host 0.0.0.0 --port 8000 --fps 15 --quality 70 --scale 0.75 --no-audio --audio-quality 5
```

Parameters:
- `--host`: IP address to bind to (default: 0.0.0.0, listens on all interfaces)
- `--port`: Port to listen on (default: 8000)
- `--fps`: Frames per second for screen capture (default: 15)
- `--quality`: JPEG compression quality from 0-100 (default: 70)
- `--scale`: Scale factor for the screen resolution (default: 0.75)
- `--no-audio`: Disable audio capture (optional)
- `--audio-quality`: Audio quality from 1-10 (default: 5)

### Starting the Client

1. Run the client on the machine that wants to view the shared screen and hear the audio:
```
python python_screen_share_client_with_audio.py
```

2. Optional parameters:
```
python python_screen_share_client_with_audio.py --host 192.168.1.100 --port 8000
```

Parameters:
- `--host`: Server IP address (default: localhost)
- `--port`: Server port (default: 8000)

3. In the client interface:
   - Enter the server IP address and port
   - Click "Connect" to start viewing the shared screen and hearing audio
   - Adjust volume using the slider
   - Toggle mute/unmute with the speaker button
   - Click "Disconnect" to stop the connection

## Advantages and Disadvantages

### Advantages Over Browser-Based Solutions

1. **Guaranteed cursor-free capture**: Uses PyAutoGUI for system-level screen capture without cursor overlay
2. **Consistent behavior across platforms**: Avoids browser inconsistencies in cursor handling
3. **Quality control**: Fine-grained adjustments for frame rate, compression quality, and scaling options
4. **Lower resource usage**: More efficient than browser-based WebRTC for simple screen sharing
5. **Simpler architecture**: Direct TCP connection rather than complex WebRTC signaling
6. **No browser dependencies**: Runs on any system with Python, even without a web browser
7. **Full control over video pipeline**: Can implement custom processing like region highlighting, annotations, or filters
8. **Reduced latency**: Direct socket connection typically has less overhead than WebRTC's complex stack
9. **Full audio support**: Captures and streams system audio alongside the screen content

### Disadvantages

1. **No NAT traversal**: Unlike WebRTC, cannot automatically connect through firewalls and NATs
2. **No built-in encryption**: Uses raw TCP sockets without encryption (can be added with TLS)
3. **Python dependency**: Requires Python and libraries on both sender and receiver
4. **Limited scalability**: Point-to-point design makes multi-viewer setups less efficient
5. **Higher setup complexity**: Requires installing Python packages vs. browser-only solution
6. **Limited mobile support**: More difficult to run on mobile devices compared to browser solutions
7. **Audio latency**: May experience audio sync issues under network congestion

## Troubleshooting

- **Connection timeouts**: Verify network connectivity and firewall settings
- **Black screen**: Ensure server is capturing properly
- **No audio**: Check that audio is enabled on server and volume is up on client
- **Audio/video sync issues**: Try reducing video quality to decrease bandwidth usage
- **PyAudio errors**: Make sure you have the proper audio drivers installed
- **High CPU usage**: Lower the FPS, scale, and audio quality values

## Future Improvements

- Add audio codec support (Opus/MP3) for better bandwidth efficiency
- Implement TLS encryption for secure connections
- Add multi-client broadcasting with server-side scaling
- Create platform-specific installers to avoid Python dependency
- Add NAT traversal functionality similar to WebRTC


## NEW GUI
Now your screen sharing hub app can work over WAN/Internet using either tunneling service - whichever one you have installed.

The changes:

Added automatic detection of cloudflared (checks if it's available on your system)
Implemented smart fallback: tries ngrok first, then cloudflared if ngrok fails or isn't installed
Added parsing of cloudflared output to extract the public URL
Ensured proper cleanup when the application closes
How to use it:
Install cloudflared if you don't have it:
powershell
winget install Cloudflare.Cloudflared
Run the hub:
powershell
.\webenv\Scripts\activate
python screen_share_hub.py
Click "Share Screen" - the hub will:
Start the streaming server
Launch a cloudflared tunnel to your port 8000
Display a public URL like random-name.trycloudflare.com:443
Automatically fill that URL in the "Host Address" field
On the remote PC, run the same Hub but:
Enter the Connection ID and Password
Paste the cloudflared URL in "Host Address"
Click "Connect"
The connection will establish directly through the Cloudflare tunnel, bypassing NAT/firewalls on both sides.

Unlike ngrok, Cloudflare Tunnel doesn't require an auth token for basic usage and has more generous free tier limits. The port forwarding happens entirely through outbound connections so it works through corporate firewalls and double-NAT setups.