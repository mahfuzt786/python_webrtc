# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WebRTCHub_Windows (clean)
Build with:
    pyinstaller WebRTCHub_clean.spec
"""

import os
from PyInstaller.utils.hooks import (
    collect_submodules,
    collect_dynamic_libs,
    collect_data_files,
    copy_metadata,
)

block_cipher = None

SCRIPT = "webrtc_gui.py"
PROJECT_DIR = os.getcwd()  # current working dir since __file__ is undefined

# Modules imported dynamically at runtime that PyInstaller may not detect
hiddenimports = (
    collect_submodules("aiortc") + [
        "aioice",
        "pyee",
        "comtypes",
        "comtypes.client",
    ]
)

# DLLs that need to ship with the executable
binaries = (
    collect_dynamic_libs("av") +
    collect_dynamic_libs("dxcam") +
    collect_dynamic_libs("pyaudio") +
    collect_dynamic_libs("cv2")
)

# Data files (e.g. certificates) required at runtime
datas = collect_data_files("aiortc") + copy_metadata("aiortc")

# ---------------------  Build blocks  ---------------------

a = Analysis(
    [SCRIPT],
    pathex=[PROJECT_DIR],
    binaries=binaries,
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
    name="WebRTCHub_Windows",
    console=True,  # show console for debug
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="WebRTCHub_Windows",
)
