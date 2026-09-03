"""Shared asset path resolution.

Split out on its own so both the runtime package and text_fit.py (which
needs FONTS_DIR for text measurement) can use it without a circular import.
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_bundled() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _assets_root() -> Path:
    """Where assets/ (compiled/, fonts/, and -- compiler-only -- template.docx)
    live.

    In a PyInstaller .app bundle, data files declared in Magenda.spec are
    unpacked under sys._MEIPASS at startup. In development (editable
    install), assets/ sits at the repo root next to pyproject.toml."""
    if is_bundled():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


REPO_ROOT = _assets_root()

# Compiler-only (scripts/compile_template.py) -- the human-editable layout
# source. The runtime package never reads this; it reads COMPILED_DIR below
# instead. Kept here anyway since paths.py is the one place both the
# compiler and the runtime resolve assets/ from.
TEMPLATE_PATH = REPO_ROOT / "assets" / "template.docx"

# Runtime: the compiled bundle scripts/compile_template.py produces --
# chrome.pdf + slots.json (see slot_schema.py) + template.docx.sha256. This
# is the only docx-derived asset the runtime package ships/reads.
COMPILED_DIR = REPO_ROOT / "assets" / "compiled"
CHROME_PDF_PATH = COMPILED_DIR / "chrome.pdf"
SLOTS_JSON_PATH = COMPILED_DIR / "slots.json"
TEMPLATE_HASH_PATH = COMPILED_DIR / "template.docx.sha256"

FONTS_DIR = REPO_ROOT / "assets" / "fonts"
