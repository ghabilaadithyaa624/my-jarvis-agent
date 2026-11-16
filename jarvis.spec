# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller Windows 11 onedir bundle specification for JARVIS PERSONAL AI."""

from pathlib import Path

root = Path(SPECPATH)

a = Analysis(
    [str(root / "jarvis" / "__main__.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "jarvis" / "config" / "default_config.yaml"), "jarvis/config"),
        (str(root / "jarvis" / "config" / "models_catalog.yaml"), "jarvis/config"),
    ],
    hiddenimports=[
        "jarvis.interface.main_window",
        "jarvis.interface.panels",
        "jarvis.interface.voice_orb",
        "jarvis.interface.dialogs",
        "jarvis.interface.theme",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JarvisPersonalAI",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JarvisPersonalAI",
)
