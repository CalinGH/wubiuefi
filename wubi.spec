# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for building wubi.exe under Wine with Python 3.12.
#
# This replaces the old pypack/pylauncher freezer. The application sources
# are staged by the Makefile into build/wubi/lib (together with the generated
# version.py), and the runtime resources (data, bin, winboot, translations)
# are staged into build/wubi/<name>. We bundle them as data files so the
# frozen application finds them under sys._MEIPASS at runtime (see src/main.py).

import os

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH is injected by PyInstaller and points at the directory of this
# spec file (the repository root).
BASE = os.path.abspath(SPECPATH)
STAGE = os.path.join(BASE, 'build', 'wubi')
LIB = os.path.join(STAGE, 'lib')

datas = [
    (os.path.join(STAGE, 'data'), 'data'),
    (os.path.join(STAGE, 'bin'), 'bin'),
    (os.path.join(STAGE, 'winboot'), 'winboot'),
    (os.path.join(STAGE, 'translations'), 'translations'),
]

# openpgp loads packet/crypto classes dynamically by name (see
# openpgp/sap/pkt/Packet.py), so static analysis cannot discover them.
# pycryptodome (the Crypto namespace) is likewise imported lazily.
hiddenimports = []
hiddenimports += collect_submodules('openpgp')
hiddenimports += collect_submodules('Crypto')

a = Analysis(
    [os.path.join(LIB, 'main.py')],
    pathex=[LIB],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
)

pyz = PYZ(a.pure)

# One-dir (COLLECT) build rather than one-file. A self-extracting one-file
# exe is one of the most common antivirus false-positive triggers (the
# PyInstaller bootloader unpacking to %TEMP% at launch). A one-dir build ships
# wubi.exe next to its DLLs/resources and is flagged far less often.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='wubi',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    # Wubi edits the Windows boot configuration (bcdedit, BCD/EFI), which
    # requires elevation. Embed a requireAdministrator manifest so launching
    # wubi.exe triggers a UAC prompt, matching the original installer.
    uac_admin=True,
    icon=os.path.join(STAGE, 'data', 'images', 'Wubi.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='wubi',
)
