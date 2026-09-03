"""Load a docx (word/document.xml plus header/footer parts) as an editable
lxml tree, the same shape the old runtime AgendaDocument used before the
LibreOffice-removal rewrite (see docs/design/remove-libreoffice-runtime-
dependency.md).

Compiler-only now: scripts/compile_template.py uses this to build the
sentinel-annotated docx fixtures it renders through LibreOffice once. The
runtime package (src/magenda/) has no docx reader at all any more -- its
working state is agenda_state.AgendaState, plain data, no XML.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from lxml import etree

from magenda.paths import REPO_ROOT, TEMPLATE_PATH
from magenda.errors import MagendaError

from .xml_ops import (
    NS,
    blank_meeting_title_slot,
    ensure_further_notes_page_break,
    remove_delegated_tasks_page,
)

__all__ = ["REPO_ROOT", "TEMPLATE_PATH", "AgendaDocument", "fresh_from_template"]

DOCUMENT_XML_PATH = "word/document.xml"

# See the module docstring in the pre-rewrite agenda_store.py (git history)
# for why these particular parts are parsed: the calendar chrome lives in
# the header, and both footers can carry themable runs even though neither
# currently carries date-specific content.
_HEADER_XML_PATH = "word/header1.xml"
_FOOTER1_XML_PATH = "word/footer1.xml"
_FOOTER2_XML_PATH = "word/footer2.xml"
_OPTIONAL_XML_PARTS = (_HEADER_XML_PATH, _FOOTER1_XML_PATH, _FOOTER2_XML_PATH)


class AgendaDocument:
    """An open docx working tree: word/document.xml plus the header/footer
    parts listed in _OPTIONAL_XML_PARTS are parsed for editing; every other
    zip entry is kept as raw bytes and written back unchanged."""

    def __init__(self, parts: dict[str, bytes], trees: dict[str, etree._ElementTree]):
        self._parts = parts
        self._trees = trees
        self.tree = trees[DOCUMENT_XML_PATH]

    @property
    def body(self) -> etree._Element:
        return self.tree.getroot().find("w:body", NS)

    @property
    def header(self) -> etree._Element | None:
        tree = self._trees.get(_HEADER_XML_PATH)
        return tree.getroot() if tree is not None else None

    def themable_trees(self) -> list[etree._ElementTree]:
        return list(self._trees.values())

    @classmethod
    def from_bytes(cls, data: bytes) -> "AgendaDocument":
        parts: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                parts[name] = zf.read(name)
        trees = {DOCUMENT_XML_PATH: etree.fromstring(parts[DOCUMENT_XML_PATH]).getroottree()}
        for path in _OPTIONAL_XML_PARTS:
            if path in parts:
                trees[path] = etree.fromstring(parts[path]).getroottree()
        return cls(parts, trees)

    @classmethod
    def load(cls, path: Path) -> "AgendaDocument":
        return cls.from_bytes(path.read_bytes())

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in self._parts.items():
                if name in self._trees:
                    data = etree.tostring(
                        self._trees[name], xml_declaration=True, encoding="UTF-8", standalone=True
                    )
                zf.writestr(name, data)
        return buf.getvalue()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())


def fresh_from_template() -> AgendaDocument:
    """A blank working copy of assets/template.docx: one blank meeting
    slot, no delegated-tasks page, the closing page-break in place -- the
    same starting point agenda_store.create() gave every date before the
    rewrite. The compiler builds every sentinel fixture from this."""
    if not TEMPLATE_PATH.exists():
        raise MagendaError(f"template not found at {TEMPLATE_PATH}")
    doc = AgendaDocument.load(TEMPLATE_PATH)
    blank_meeting_title_slot(doc.body)
    ensure_further_notes_page_break(doc.body)
    remove_delegated_tasks_page(doc.body)
    return doc
