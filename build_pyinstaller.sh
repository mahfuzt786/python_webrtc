#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# build_pyinstaller.sh  –  Create a single-file PyInstaller build + ZIP bundle
# -----------------------------------------------------------------------------
# This produces:
#   dist/webrtchub            (self-contained executable)
#   webrtchub-linux.zip       (zip containing the executable + README)
#
# Requirements (Ubuntu/Debian):
#   sudo apt-get install python3-venv python3-pip build-essential \
#        libsrtp2-dev portaudio19-dev ffmpeg libffi-dev libssl-dev
#
# Usage:
#   ./build_pyinstaller.sh
# -----------------------------------------------------------------------------
set -euo pipefail

if [[ $(id -u) -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi

# Ensure venv exists
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip
pip install pyinstaller==6.5.0 -r requirements.txt

# Build
pyinstaller --clean --onefile --name webrtchub WebRTCHub_linux.spec

# Zip output for distribution
mkdir -p release
cp dist/webrtchub release/
cat > release/README.txt <<EOF
WebRTC Hub (Linux single-file binary)
====================================
1. Make executable:  chmod +x webrtchub
2. Run:             ./webrtchub
EOF
cd release
zip -9r ../webrtchub-linux.zip ./*
cd ..

echo "\n[SUCCESS] Built dist/webrtchub and webrtchub-linux.zip"
