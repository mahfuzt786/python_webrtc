FROM python:3.9-slim

# Install system dependencies for opencv, pyaudio and X11 support
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libasound2-dev \
    portaudio19-dev \
    x11-apps \
    python3-tk \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy requirements first (for better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the server port
EXPOSE 8000
# Expose the UDP discovery port
EXPOSE 54545/udp

# Command to run the server in headless mode
CMD ["python", "screen_share_server.py"]
