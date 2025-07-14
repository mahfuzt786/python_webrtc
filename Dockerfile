# ---------------------------------------------
# WebRTC Screen Share Hub – Docker Image
# ---------------------------------------------
# This image runs ONLY the signalling server. The GUI parts of the
# application require a local display and are therefore meant to be run
# on your desktop, not inside Docker.
#
# Build:
#     docker build -t webrtc-hub .
#
# Run:
#     docker run -d --name webrtc_signalling -p 8765:8765 webrtc-hub
# ---------------------------------------------
FROM python:3.12-slim

# ------------- System packages ---------------
RUN apt-get update && apt-get install -y \
    libasound2-dev \
    portaudio19-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ------------- Project setup -----------------
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ------------- Expose Ports ------------------
EXPOSE 8765

# ------------- Default command ---------------
# Listen on all interfaces so clients on other machines can reach it.
CMD ["python", "signalling_server.py", "--host", "0.0.0.0", "--port", "8765"]
