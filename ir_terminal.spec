# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for IR-OWC Lab Terminal standalone distribution."""

from pathlib import Path
import sys

block_cipher = None
project_root = Path(SPECPATH).resolve() if "SPECPATH" in globals() else Path.cwd().resolve()

# Bundle web assets (HTML, CSS, JS, local TTF fonts) once; the runtime
# resolver (path_helpers.get_web_index_path) checks both frozen layouts.
datas = [
    (str(project_root / 'src' / 'ir_terminal' / 'ui' / 'web'), 'ir_terminal/ui/web'),
]

hiddenimports = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebChannel',
    'serial',
    'serial.tools',
    'serial.tools.list_ports',
    'zlib',
    'csv',
    'json',
]

a = Analysis(
    ['src/ir_terminal/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='IR_OWC_Lab_Terminal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='IR_OWC_Lab_Terminal',
)
