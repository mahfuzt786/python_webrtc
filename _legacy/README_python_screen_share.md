# Python Screen Sharing with Audio Support

This solution provides cursor-free screen sharing between devices using Python with full audio support. Unlike browser-based WebRTC solutions, this implementation ensures that the mouse cursor is never visible in the shared screen by leveraging system-level screen capture, while also streaming system audio for a complete sharing experience.

## System Components

1. **Screen Share Server (`python_screen_share_server_with_audio.py`)**: 
   - Captures the screen without cursor 
   - Records system audio
   - Streams both to connected clients

2. **Screen Share Client (`python_screen_share_client_with_audio.py`)**:
   - Connects to the server
   - Displays the shared screen
   - Plays synchronized audio with volume control

## Audio Features

- **System Audio Capture and Playback**: Streams audio from sender's system to receivers
- **Volume Control**: Adjust volume level on the client side
- **Mute/Unmute**: Toggle audio playback without disconnecting
- **Synchronized A/V**: Audio and video are transmitted separately but synchronized on playback
- **Bandwidth Optimization**: Configurable audio quality settings

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
  - Total expected usage: 45-80% of one CPU core at default settings (15 FPS, 0.75 scaling)

- **Client-side**:
  - JPEG decompression: 10-20% of one core
  - Frame rendering: 5-15% of one core
  - **Audio playback**: Additional 3-7% CPU usage
  - Total expected usage: 18-42% of one CPU core

### Compression and Encoding Details

- **Image Compression**: 
  - **Algorithm**: DCT-based JPEG compression (ISO/IEC 10918)
  - **Library**: Uses libjpeg via OpenCV's imencode/imdecode functions
  - **Quality control**: Variable compression ratio via IMWRITE_JPEG_QUALITY parameter (0-100)
  - **Process**: 
     1. RGB to YCbCr color space conversion
     2. Chroma subsampling (typically 4:2:0 in default mode)
     3. 8x8 block DCT transform
     4. Quantization (quality-dependent)
     5. Zigzag ordering and entropy coding (Huffman)

- **Frame Pipeline**:
  1. **Capture**: Raw RGB/BGR pixel data from PyAutoGUI (uncompressed)
  2. **Pre-processing**: 
     - RGB/BGR color conversion via OpenCV
     - Rescaling using bilinear interpolation
  3. **Encoding**: JPEG compression with configurable quality
  4. **Transport**: Binary frame transmission with length prefix
  5. **Decoding**: JPEG decompression to raw pixel data
  6. **Display**: Conversion to Tkinter PhotoImage for rendering

- **Codec Selection Rationale**:
  - JPEG chosen for balance of quality, compression ratio, and processing speed
  - Single-frame compression avoids inter-frame dependencies, making the system more resilient to packet loss
  - Hardware acceleration possible on some platforms via libjpeg-turbo

### Audio Codec Details:
  - Raw PCM audio (uncompressed) for simplicity and low latency
  - 16-bit signed integer format provides excellent quality
  - Future improvement: Implement Opus/MP3 compression for lower bandwidth usage

### GPU Usage

- **Default configuration**: Minimal GPU usage as operations are CPU-bound
- **With OpenCV GPU acceleration** (requires separate build of OpenCV with CUDA):
  - Can offload scaling and compression to GPU, reducing CPU usage by 20-40%
  - Server requires NVIDIA GPU with CUDA support for acceleration

### Network Bandwidth

Bandwidth usage depends on settings but typical values are:

| Resolution | Scale | Video Quality | FPS | Audio Quality | Total Bandwidth |
|------------|-------|--------------|-----|--------------|----------------|
| 1920x1080  | 0.75  | 70           | 15  | Medium       | ~5.2 Mbps      |
| 1920x1080  | 0.50  | 50           | 10  | Low          | ~2.1 Mbps      |
| 1920x1080  | 1.00  | 90           | 30  | High         | ~20.8 Mbps     |

### Memory Usage

- **Server**: 150-300 MB RAM + ~20 MB for audio processing (higher with larger resolutions)
- **Client**: 100-200 MB RAM + ~15 MB for audio buffering

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
6. **Audio sync challenges**: May experience audio/video sync issues under high network load
7. **Limited mobile support**: More difficult to run on mobile devices compared to browser solutions

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

## Network Requirements

- Both devices must be able to connect to each other on the configured port
- For local network use, devices must be on the same network
- For internet use, the server's port must be forwarded through any firewalls/routers

## Performance Tuning

### For Better Quality
- Increase the `--quality` parameter (up to 100)
- Use a higher `--scale` value (up to 1.0 for full resolution)

### For Lower Bandwidth
- Decrease the `--quality` parameter
- Lower the `--scale` value
- Reduce the `--fps` parameter

## Security Considerations

- This implementation uses unencrypted TCP connections
- For sensitive information, consider adding authentication and encryption
- For internal network use only, or use with VPN for internet connections

## Troubleshooting

- **Connection timeouts**: Verify network connectivity and firewall settings
- **Black screen**: Ensure server is capturing properly
- **High latency**: Adjust quality, scale, and FPS parameters
- **High CPU usage**: Lower the FPS and scale values
