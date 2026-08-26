"""Locate the LibreOffice ('soffice') binary used to render docx -> PDF.

Split out on its own (rather than living in tools/render.py, where it
originated) so both tools/render.py and theme.py can depend on it without
either depending on the other — theme.py needs to shell out to soffice for
render_pdf_with_theme, and tools/render.py needs theme.py to apply the
active config before its own soffice call; if find_soffice stayed in
tools/render.py, importing it from theme.py would pull in the whole
magenda.tools package (via tools/__init__.py's eager submodule imports),
which itself imports tools/render.py, which imports theme.py — circular.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from magenda.xml_ops import MagendaError

_SOFFICE_CANDIDATES = [
    "soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/bin/soffice",
]


def find_soffice() -> str:
    for candidate in _SOFFICE_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    raise MagendaError(
        "LibreOffice ('soffice') was not found. Install it (e.g. `brew install --cask "
        "libreoffice` on macOS) so agendas can be rendered to PDF deterministically."
    )
