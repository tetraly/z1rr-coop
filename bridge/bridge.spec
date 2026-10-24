# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for bridge.exe (Windows-first)."""

from pathlib import Path

block_cipher = None

a = Analysis(
    ['bridge_gui/__main__.py'],
    pathex=[str(Path('.').resolve())],
    binaries=[],
    datas=[
        ('assets/ganon_blue.ico', 'assets'),
        ('bridge_core/patches/zelda_z1rr_coop.ips', 'bridge_core/patches'),
        ('bridge_core/patches/zelda_z1rr_coop.expected.json', 'bridge_core/patches'),
        ('bridge_core/patches/zelda_z1rr_coop_beta8.ips', 'bridge_core/patches'),
        ('bridge_core/patches/zelda_z1rr_coop_beta8.expected.json', 'bridge_core/patches'),
        ('bridge_core/mister_payload/manifest.json', 'bridge_core/mister_payload'),
        ('bridge_core/mister_payload/mister-helper.py', 'bridge_core/mister_payload'),
        ('bridge_core/mister_payload/NES_z1rr-coop.rbf', 'bridge_core/mister_payload'),
        ('tools/edlink-n8.exe', 'tools'),
    ],
    hiddenimports=[
        'bridge_core.modes.tloz_basic',
        'bridge_core.modes.tloz_progress',
        'bridge_core.modes.tloz_all',
        'serial.tools.list_ports_windows',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/ganon_blue.ico',
)
