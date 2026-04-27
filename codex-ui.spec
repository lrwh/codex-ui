# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import PySide6


PYSIDE_DIR = Path(PySide6.__file__).resolve().parent


def plugin_binaries(relative_dir):
    source_dir = PYSIDE_DIR / relative_dir
    return [
        (str(path), f"PySide6/{relative_dir}")
        for path in source_dir.glob("*")
        if path.is_file()
    ]


a = Analysis(
    ["desktop_app.py"],
    pathex=[],
    binaries=[
        *plugin_binaries("Qt/plugins/platforms"),
        *plugin_binaries("Qt/plugins/platformthemes"),
        *plugin_binaries("Qt/plugins/platforminputcontexts"),
    ],
    datas=[("packaging/codex-ui.svg", "."), ("VERSION", ".")],
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
    [],
    exclude_binaries=True,
    name="codex-ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="codex-ui",
)
