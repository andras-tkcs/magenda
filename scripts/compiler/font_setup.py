"""Ensure the bundled fonts are available to LibreOffice for the one-off
template compile (scripts/compile_template.py).

Compiler-only, same reasoning as soffice.py in this directory: the runtime
package never installs fonts system-wide -- pdf_assembler.py hands pymupdf
the .ttf file directly on every insert_text() call, so no OS-level install
or fontconfig cache refresh is needed (or done) at runtime.

The .ttf files in assets/fonts/ are generated once (see assets/fonts/README,
or regenerate with scripts/build_fonts.py) from Google's canonical Outfit
variable font, with name-table entries set so each weight resolves under the
exact family name the compiler's sentinel-annotated docx references (e.g.
"Outfit Black").
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from magenda.paths import FONTS_DIR


def _user_font_dir() -> Path | None:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Fonts"
    if system == "Linux":
        return Path.home() / ".local" / "share" / "fonts"
    if system == "Windows":
        return Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    return None


def ensure_fonts_installed() -> list[Path]:
    """Copy any bundled font not already present (by filename) into the
    user's font directory. Idempotent — safe to call before every render."""
    target_dir = _user_font_dir()
    if target_dir is None:
        return []
    target_dir.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    for font_file in sorted(FONTS_DIR.glob("*.ttf")):
        dest = target_dir / font_file.name
        if not dest.exists() or dest.stat().st_size != font_file.stat().st_size:
            shutil.copyfile(font_file, dest)
            installed.append(dest)

    if installed and platform.system() == "Linux" and shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f", str(target_dir)], capture_output=True, check=False)

    return installed
