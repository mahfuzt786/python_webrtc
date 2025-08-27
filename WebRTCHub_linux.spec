# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WebRTC Hub (Linux, one-file)
Build with:
    pyinstaller WebRTCHub_linux.spec
Prereqs (Ubuntu):
    sudo apt-get install python3-venv python3-pip build-essential \
         libsrtp2-dev portaudio19-dev ffmpeg libffi-dev libssl-dev
"""
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

SCRIPT = "webrtc_gui.py"
PROJECT_DIR = os.getcwd()

hiddenimports = collect_submodules("aiortc") + [
    "aioice",
    "pyee",
]

datas = collect_data_files("aiortc")

# ---------------------------------------------------------------------------
a = Analysis(
    [SCRIPT],
    pathex=[PROJECT_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="webrtchub",
    console=True,
    strip=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    name="webrtchub",
)

# OneFile wrapper
onefile = BUNDLE(coll, name="webrtchub", format="onefile", compressed=True)
