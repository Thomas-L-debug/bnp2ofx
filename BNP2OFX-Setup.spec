# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

packed = os.environ.get("BNP2OFX_PACKED")
if not packed:
    packed = str(Path("dist") / "BNP2OFX.exe")

a = Analysis(
    ["installer/setup_app.py"],
    pathex=[],
    binaries=[],
    datas=[(packed, ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BNP2OFX-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets\\icon.ico",
    contents_directory=".",
)
