"""Shared asset path resolution.

Split out from agenda_store.py so xml_ops.py (which needs FONTS_DIR for text
measurement) can use it without agenda_store.py <-> xml_ops.py becoming a
circular import (agenda_store.py already imports from xml_ops.py).
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_bundled() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _assets_root() -> Path:
    """Where assets/ (template.docx, fonts/) live.

    In a PyInstaller .app bundle, data files declared in Magenda.spec are
    unpacked under sys._MEIPASS at startup. In development (editable
    install), assets/ sits at the repo root next to pyproject.toml."""
    if is_bundled():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


REPO_ROOT = _assets_root()
TEMPLATE_PATH = REPO_ROOT / "assets" / "template.docx"
FONTS_DIR = REPO_ROOT / "assets" / "fonts"
