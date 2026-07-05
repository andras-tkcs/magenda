"""Load/save the working docx for a given date. The server is otherwise
stateless: every tool call loads by date, mutates the in-memory tree, saves
by date. The on-disk template (assets/template.docx) is never mutated."""
from __future__ import annotations

import datetime
import zipfile
from pathlib import Path

from lxml import etree

from magenda.paths import FONTS_DIR, REPO_ROOT, TEMPLATE_PATH
from magenda.xml_ops import (
    NS,
    MagendaError,
    blank_meeting_title_slot,
    ensure_further_notes_page_break,
    strip_meeting_notes_footer,
)

__all__ = ["FONTS_DIR", "REPO_ROOT", "TEMPLATE_PATH", "AGENDA_DIR", "AgendaDocument"]

AGENDA_DIR = Path.home() / ".magenda" / "agendas"

DOCUMENT_XML_PATH = "word/document.xml"


class AgendaDocument:
    """An open docx working tree: the document.xml part is parsed for
    editing, every other zip entry is kept as raw bytes and written back
    unchanged."""

    def __init__(self, parts: dict[str, bytes], tree: etree._ElementTree):
        self._parts = parts
        self.tree = tree

    @property
    def body(self) -> etree._Element:
        return self.tree.getroot().find("w:body", NS)

    @classmethod
    def from_bytes(cls, data: bytes) -> "AgendaDocument":
        parts: dict[str, bytes] = {}
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            for name in zf.namelist():
                parts[name] = zf.read(name)
        tree = etree.fromstring(parts[DOCUMENT_XML_PATH]).getroottree()
        return cls(parts, tree)

    @classmethod
    def load(cls, path: Path) -> "AgendaDocument":
        return cls.from_bytes(path.read_bytes())

    def to_bytes(self) -> bytes:
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in self._parts.items():
                if name == DOCUMENT_XML_PATH:
                    data = etree.tostring(self.tree, xml_declaration=True, encoding="UTF-8", standalone=True)
                zf.writestr(name, data)
        return buf.getvalue()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())


def docx_path(date: datetime.date) -> Path:
    return AGENDA_DIR / f"{date.isoformat()}.docx"


def pdf_path(date: datetime.date) -> Path:
    return AGENDA_DIR / f"{date.isoformat()}.pdf"


def create(date: datetime.date) -> AgendaDocument:
    if not TEMPLATE_PATH.exists():
        raise MagendaError(f"template not found at {TEMPLATE_PATH}")
    doc = AgendaDocument.load(TEMPLATE_PATH)
    blank_meeting_title_slot(doc.body)
    strip_meeting_notes_footer(doc.body)
    ensure_further_notes_page_break(doc.body)
    return doc


def load(date: datetime.date) -> AgendaDocument:
    target = docx_path(date)
    if not target.exists():
        raise MagendaError(f"no agenda exists for {date.isoformat()} yet; call create_agenda first")
    return AgendaDocument.load(target)


def save(date: datetime.date, doc: AgendaDocument) -> Path:
    target = docx_path(date)
    doc.save(target)
    return target


def docx_exists(date: datetime.date) -> bool:
    return docx_path(date).exists()
