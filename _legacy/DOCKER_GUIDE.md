# Docker Setup for Python Screen Share

This guide explains how to use the Docker configuration for the Python Screen Share application.

## Docker Setup Overview

The Docker configuration provides:
- Containerized screen share server
- Network port exposure for TCP streaming (8000) and UDP discovery (54545)
- Environment variable configuration for connection ID and password
- Optional Cloudflared integration for WAN/NAT traversal

## Requirements

- Docker and Docker Compose installed on your system
- For GUI client access: Python environment with required dependencies

## Files Included

- `Dockerfile`: Defines the container image with all dependencies
- `docker-compose.yml`: Configures services and networking
- `screen_share_server.py`: Headless server optimized for Docker

## Usage

### Starting the Server (Headless Mode)

```bash
# Build and start the container
docker-compose up --build
```

This will:
1. Build the Docker image with all required dependencies
2. Start the screen share server on port 8000
3. Enable UDP discovery on port 54545
4. Display connection ID and password in logs

### Customizing Connection Parameters

Edit the `docker-compose.yml` file to change default connection settings:

```yaml
environment:
  - CONNECTION_ID=123456  # Change this
  - PASSWORD=1234         # Change this
```

Or set them at runtime:

```bash
docker-compose run -e CONNECTION_ID=654321 -e PASSWORD=5678 screen-share-server
```

### Connecting as a Client

Use the standard Python client GUI to connect to the Docker server:

1. Run `python screen_share_hub.py` on your desktop/client machine
2. Enter the Connection ID and Password displayed in the server logs
3. For direct connection, enter the server's IP address in the "Host Address" field
4. Click "Connect"

### Using with Cloudflared for WAN Access

To enable Internet access to your Docker server:

1. Uncomment the cloudflared service in `docker-compose.yml`
2. Start both services: `docker-compose up`
3. Look for the Cloudflare URL in the logs
4. Share this URL along with the Connection ID and Password

## Limitations

- The Docker container cannot display its own GUI
- Screen sharing from Docker requires X11 forwarding (commented out in docker-compose.yml)
- Audio capture inside Docker requires additional configuration

## Troubleshooting

### Network Issues

If clients cannot discover the server:
- Ensure port forwarding is correct: `docker-compose ps`
- Check that UDP broadcast is not blocked by firewalls
- Try direct connection using "Host Address" field

### WAN Connection Issues

- Verify Cloudflared is running: `docker-compose logs cloudflared`
- Check for tunnel errors in logs
- Ensure the tunnel URL is correctly formatted (copy from logs)

### Performance Issues

- Adjust frame rate by setting the FRAME_RATE environment variable
- Reduce image quality by modifying the JPEG quality value in the server code
