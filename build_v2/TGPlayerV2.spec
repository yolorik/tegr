# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['mpv', 'cryptg', 'python_socks', 'socks']
hiddenimports += collect_submodules('python_socks')
hiddenimports += collect_submodules('telethon')


a = Analysis(
    ['C:/tgplayer/player_deepseek_v2.py'],
    pathex=[],
    binaries=[('C:\\tgplayer\\libmpv-2.dll', '.'), ('C:\\tgplayer\\vulkan-1.dll', '.')],
    datas=[],
    hiddenimports=hiddenimports,
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
    name='TGPlayerV2',
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
    version='C:\\tgplayer\\version_info_gen.txt',
    icon=['C:\\tgplayer\\tgplayer.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TGPlayerV2',
)
