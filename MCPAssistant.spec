# -*- mode: python ; coding: utf-8 -*-

import sys
import os

block_cipher = None

project_dir = os.path.abspath(SPECPATH)

datas = [
    (os.path.join(project_dir, 'src'), 'src'),
    (os.path.join(project_dir, 'LICENSE'), '.'),
    (os.path.join(project_dir, 'NO-SALE-ADDENDUM.md'), '.'),
    (os.path.join(project_dir, 'NOTICE.md'), '.'),
    (os.path.join(project_dir, 'THIRD_PARTY_NOTICES.md'), '.'),
    (os.path.join(project_dir, 'QT_LGPL_COMPLIANCE.md'), '.'),
    (os.path.join(project_dir, 'PRIVACY.md'), '.'),
    (os.path.join(project_dir, 'licenses'), 'licenses'),
]

a = Analysis(
    [os.path.join(project_dir, 'src', 'main.py')],
    pathex=[project_dir],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'websockets',
        'mcp',
        'mcp.server',
        'mcp.server.fastmcp',
        'pydantic',
        'pydantic.deprecated.decorator',
        'requests',
        'asyncio',
        'ctypes',
        'ctypes.wintypes',
        'PIL',
        'PIL.Image',
        'pythoncom',
        'pywintypes',
        'win32api',
        'win32com',
        'win32com.client',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtSvg',
        'shiboken6',
        'src.catalog.window_control',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MCPAssistant',
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
    icon=os.path.join(project_dir, 'src', 'ui_qt', 'resources', 'branding', 'mcp_assistant.ico'),
    manifest=os.path.join(project_dir, 'installer', 'MCPAssistant.manifest'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MCPAssistant',
)
