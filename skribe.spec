# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Skribe.

Build with:
    pip install pyinstaller          # one-time, into the same venv as Skribe
    pyinstaller skribe.spec --clean  # produces dist/Skribe (or Skribe.app on macOS)

Notes:
  * pandoc, LibreOffice (soffice), and hunspell dictionaries are NOT bundled.
    Skribe shells out to them at runtime; install them on the target machine
    the same way `requirements.txt` describes for source installs.
  * Cross-compiling is not supported. Build on each target OS (Windows /
    macOS / Linux x86_64 / Linux aarch64 for the Pi 5).
  * The icon arg below is conditional: Windows wants .ico, macOS wants .icns,
    Linux uses the SVG via the QApplication window-icon. If you haven't
    generated platform-native icon files yet, the build still succeeds — it
    just falls back to a generic OS app icon and the in-app icon (the SVG
    set on QApplication) keeps working.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve()
ENTRY = str(PROJECT_ROOT / "skribe" / "__main__.py")

# Bundled assets — paths are (source, destination-inside-bundle).
# Destination paths mirror the in-source layout so existing
# `Path(__file__).parent / "resources" / ...` lookups keep working.
datas = [
    (str(PROJECT_ROOT / "skribe" / "resources" / "icons"),
     "skribe/resources/icons"),
    (str(PROJECT_ROOT / "skribe" / "resources" / "textures"),
     "skribe/resources/textures"),
]

# Per-platform native icon. Drop these files in place if/when you have them.
_icon_candidates = {
    "win32":  PROJECT_ROOT / "skribe" / "resources" / "icons" / "skribe.ico",
    "darwin": PROJECT_ROOT / "skribe" / "resources" / "icons" / "skribe.icns",
}
_icon_path = _icon_candidates.get(sys.platform)
icon_arg = str(_icon_path) if _icon_path and _icon_path.is_file() else None


a = Analysis(
    [ENTRY],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Excluding obviously-unused Qt modules trims ~30–50 MB. Add others
    # only after verifying nothing in your codepath touches them — Qt is
    # heavily interconnected and a wrong exclude triggers cryptic
    # ImportError at first use of a feature.
    excludes=[
        "PySide6.QtBluetooth",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQuickWidgets",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
    ],
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
    name="Skribe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX corrupts Qt binaries on some platforms; leave off.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # GUI app — no terminal window on Windows.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Skribe",
)

# macOS .app bundle — only emitted on darwin. The bundle wraps the COLLECT
# output and gives Finder/Dock something to launch.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Skribe.app",
        icon=icon_arg,
        bundle_identifier="com.kryptonmediagroup.skribe",
        info_plist={
            "CFBundleName": "Skribe",
            "CFBundleDisplayName": "Skribe",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
