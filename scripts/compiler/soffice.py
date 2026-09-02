"""Locate the LibreOffice ('soffice') binary.

Compiler-only: LibreOffice is invoked exactly once, by
scripts/compile_template.py, to turn assets/template.docx into the
compiled bundle under assets/compiled/ (see that script's module
docstring). Nothing under src/magenda/ -- the shipped runtime package --
imports this module or shells out to soffice; that's the whole point of
the compiled bundle.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from magenda.errors import MagendaError

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
