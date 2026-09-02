# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Magenda.
#
# Produces:
#   dist/Magenda/magenda   ← stdio MCP server (Claude's entry point),
#                             packed into a .mcpb by scripts/build_mcpb.sh
#
# Build:
#   pip install pyinstaller
#   pyinstaller Magenda.spec
#
# Notes:
#   - Run on the target architecture. For Apple Silicon: arch -arm64 pyinstaller ...
#   - Code-signing is handled by build_mcpb.sh. No macOS .app bundle is built —
#     Claude launches the executable directly as an MCP subprocess, never via
#     Finder/Launch Services, so app-bundle chrome (Info.plist, icon, etc.)
#     isn't needed.
#   - No external app dependency at all: rendering is pure Python
#     (pymupdf + the bundled fonts, see src/magenda/pdf_assembler.py) against
#     the compiled template (assets/compiled/, produced once by
#     scripts/compile_template.py -- see docs/design/remove-libreoffice-
#     runtime-dependency.md). assets/template.docx itself is the human-
#     editable design source and isn't bundled -- the runtime never reads it.

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

SRC = str(Path("src").resolve())
sys.path.insert(0, SRC)

datas = [
    ("assets/compiled", "assets/compiled"),
    ("assets/fonts", "assets/fonts"),
    *collect_data_files("mcp"),
    *copy_metadata("mcp"),
]

hidden_imports = [
    "mcp",
    "mcp.server.fastmcp",
    "pymupdf",
    "pydantic",
    "pydantic_core",
    "PIL",
    "PIL.ImageFont",
]

a = Analysis(
    ["src/_entry.py"],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="magenda",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,       # speaks MCP over stdio — must be a console app
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Magenda",
)
