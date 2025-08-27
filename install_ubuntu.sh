#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# WebRTC Hub – Linux/Ubuntu installer
# -----------------------------------------------------------------------------
# This script installs all required system packages, sets up a Python virtual
# environment in the project directory, installs Python dependencies from
# requirements.txt, and creates a simple launcher script.
#
# Usage (from project root):
#   chmod +x install_ubuntu.sh
#   ./install_ubuntu.sh
# -----------------------------------------------------------------------------
set -euo pipefail

if [[ $(id -u) -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

# Detect Ubuntu (or one of its derivatives) quickly
if ! grep -qi "ubuntu" /etc/os-release; then
  echo "[ERROR] This installer is intended for Ubuntu or Ubuntu-based distros." >&2
  echo "        For other distributions, install equivalent packages manually." >&2
  exit 1
fi

echo "[INFO] Installing system dependencies…"
$SUDO apt-get update -y
$SUDO apt-get install -y \
    python3 python3-venv python3-pip build-essential \
    libssl-dev libffi-dev libsrtp2-dev \
    libportaudio2 portaudio19-dev \
    ffmpeg

echo "[INFO] Creating local Python virtual environment (.venv)…"
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip first
pip install --upgrade pip

echo "[INFO] Installing Python dependencies…"
pip install -r requirements.txt

echo "[INFO] Creating launcher script run-webrtc-hub…"
cat > run-webrtc-hub <<'EOF'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/.venv/bin/activate"
python "$DIR/webrtc_gui.py" "$@"
EOF
chmod +x run-webrtc-hub

echo "\n[SUCCESS] Installation completed."
echo "Run ./run-webrtc-hub to start the WebRTC Hub GUI."
