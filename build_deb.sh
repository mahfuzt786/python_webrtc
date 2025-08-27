#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# build_deb.sh  –  Build a .deb package for WebRTC Hub
# -----------------------------------------------------------------------------
# Prerequisites (Ubuntu/Debian):
#   sudo apt-get install dpkg-dev build-essential python3-venv python3-pip rsync
#
# Usage:
#   ./build_deb.sh             # builds webrtchub_<version>.deb in current dir
#   ./build_deb.sh 2.3.1       # override version number
# -----------------------------------------------------------------------------
set -euo pipefail

NAME="webrtchub"
VERSION="${1:-1.0.0}"
ARCH="all"

BUILD_DIR="$NAME-$VERSION"
INSTALL_ROOT="/usr/share/$NAME"

# Clean previous build dir if present
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR$INSTALL_ROOT"
mkdir -p "$BUILD_DIR/usr/bin"

# -------------------- 1. control file ----------------------------------------
cat > "$BUILD_DIR/DEBIAN/control" <<EOF
Package: $NAME
Version: $VERSION
Section: video
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.10), python3-venv, python3-pip, ffmpeg, portaudio19-dev, libsrtp2-1, libpulse0, libffi8, libssl3
Maintainer: Your Name <you@example.com>
Homepage: https://github.com/your-org/webrtchub
Description: WebRTC Screen Share Hub (host & viewer)
 A simple cross-platform GUI for real-time screen sharing using WebRTC.
EOF

# -------------------- 2. postinst script (venv setup) ------------------------
cat > "$BUILD_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
INSTALL_ROOT="/usr/share/webrtchub"
cd "$INSTALL_ROOT"

# Create venv only if not present
if [ ! -d .venv ]; then
  python3 -m venv .venv
  . .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
fi
exit 0
EOF
chmod 0755 "$BUILD_DIR/DEBIAN/postinst"

# -------------------- 3. copy project files ----------------------------------
# Exclude Windows-specific and build scripts to reduce size
if command -v rsync >/dev/null 2>&1; then
  rsync -av --exclude="*.spec" --exclude="*.exe" --exclude="build_deb.sh" --exclude="install_ubuntu.sh" ./ "${BUILD_DIR}${INSTALL_ROOT}/"
else
  echo "[WARN] rsync not found – installing (requires sudo)…"
  if [ $(id -u) -eq 0 ]; then
    apt-get update -y && apt-get install -y rsync
  else
    sudo apt-get update -y && sudo apt-get install -y rsync
  fi
  rsync -av --exclude="*.spec" --exclude="*.exe" --exclude="build_deb.sh" --exclude="install_ubuntu.sh" ./ "${BUILD_DIR}${INSTALL_ROOT}/"
fi

# -------------------- 4. launcher -------------------------------------------
cat > "$BUILD_DIR/usr/bin/webrtchub" <<'EOF'
#!/usr/bin/env bash
INSTALL_ROOT="/usr/share/webrtchub"
cd "$INSTALL_ROOT"
source "$INSTALL_ROOT/.venv/bin/activate" 2>/dev/null || true
python "$INSTALL_ROOT/webrtc_gui.py" "$@"
EOF
chmod 0755 "$BUILD_DIR/usr/bin/webrtchub"

# -------------------- 5. build the .deb -------------------------------------
echo "[INFO] Building .deb package…"
dpkg-deb --build "$BUILD_DIR"

echo "\n[SUCCESS] Package built: ${BUILD_DIR}.deb"
echo "Install with:  sudo apt install ./${BUILD_DIR}.deb"
