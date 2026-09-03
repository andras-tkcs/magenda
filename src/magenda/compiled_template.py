"""Load the compiled-template bundle (assets/compiled/) that
scripts/compile_template.py produces: chrome.pdf, the blank vector-only
page shells, and slots.json, the geometry manifest (see slot_schema.py).

This is the only place the runtime touches anything docx-derived. Loaded
once and cached -- the bundle is a build artifact, not something that
changes while the server runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import pymupdf

from magenda.errors import MagendaError
from magenda.paths import CHROME_PDF_PATH, SLOTS_JSON_PATH
from magenda.slot_schema import CompiledManifest


@dataclass(frozen=True)
class CompiledTemplate:
    manifest: CompiledManifest
    chrome_bytes: bytes  # raw chrome.pdf bytes -- pdf_assembler opens a fresh pymupdf.Document per render


@lru_cache(maxsize=1)
def load() -> CompiledTemplate:
    if not SLOTS_JSON_PATH.exists() or not CHROME_PDF_PATH.exists():
        raise MagendaError(
            f"no compiled template bundle at {SLOTS_JSON_PATH.parent} -- run "
            "`python scripts/compile_template.py` once (needs a local LibreOffice "
            "install) and commit its output before rendering agendas. See "
            "docs/design/remove-libreoffice-runtime-dependency.md."
        )
    manifest = CompiledManifest.from_dict(json.loads(SLOTS_JSON_PATH.read_text()))
    chrome_bytes = CHROME_PDF_PATH.read_bytes()
    # Fail fast on a corrupt/truncated bundle rather than on the first render.
    with pymupdf.open(stream=chrome_bytes, filetype="pdf"):
        pass
    return CompiledTemplate(manifest=manifest, chrome_bytes=chrome_bytes)
