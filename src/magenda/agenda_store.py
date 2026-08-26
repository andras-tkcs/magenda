"""Load/save the working docx for a given date. The server is otherwise
stateless: every tool call loads by date, mutates the in-memory tree, saves
by date. The working document itself lives only in this process's memory
(`_STORE` below) -- nothing touches disk until render_pdf is explicitly
asked to keep a copy. The on-disk template (assets/template.docx) is never
mutated."""
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
    remove_delegated_tasks_page,
)

__all__ = ["FONTS_DIR", "REPO_ROOT", "TEMPLATE_PATH", "AgendaDocument"]

DOCUMENT_XML_PATH = "word/document.xml"

# The calendar chrome (day/weekday/CW/month/year) now lives in the Word
# header part instead of being cloned into the body once per page (see
# xml_ops.find_calendar_block) -- editing it once here is what makes that
# "enough if edited once" true. footer1.xml is the delegated-tasks page's
# "Notes and updates" footer and footer2.xml is the plain footer used
# everywhere else; neither currently carries date-specific content, but both
# are parsed anyway so font-pack/color theming (see theme.py) can reach the
# runs living in them too. Any of these may be absent (e.g. a hand-built docx
# in a test fixture) -- parsing is best-effort per part.
_HEADER_XML_PATH = "word/header1.xml"
_FOOTER1_XML_PATH = "word/footer1.xml"
_FOOTER2_XML_PATH = "word/footer2.xml"
_OPTIONAL_XML_PARTS = (_HEADER_XML_PATH, _FOOTER1_XML_PATH, _FOOTER2_XML_PATH)

# Working agendas, keyed by date. Process-lifetime only -- an agenda that
# hasn't been rendered/exported is lost if the server restarts.
_STORE: dict[datetime.date, "AgendaDocument"] = {}


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
        """The document's Word header part (w:hdr root), or None if this
        docx doesn't have one."""
        tree = self._trees.get(_HEADER_XML_PATH)
        return tree.getroot() if tree is not None else None

    def themable_trees(self) -> list[etree._ElementTree]:
        """Every parsed XML part that can carry font/color runs -- what
        theme.apply_theme_to_document loops over, so the calendar header and
        the "Notes and updates"/plain footers get themed too, not just
        document.xml's body content."""
        return list(self._trees.values())

    @classmethod
    def from_bytes(cls, data: bytes) -> "AgendaDocument":
        parts: dict[str, bytes] = {}
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
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
        import io

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


def create(date: datetime.date) -> AgendaDocument:
    if not TEMPLATE_PATH.exists():
        raise MagendaError(f"template not found at {TEMPLATE_PATH}")
    doc = AgendaDocument.load(TEMPLATE_PATH)
    blank_meeting_title_slot(doc.body)
    ensure_further_notes_page_break(doc.body)
    remove_delegated_tasks_page(doc.body)
    return doc


def load(date: datetime.date) -> AgendaDocument:
    doc = _STORE.get(date)
    if doc is None:
        raise MagendaError(f"no agenda exists for {date.isoformat()} yet; call create_agenda first")
    return doc


def save(date: datetime.date, doc: AgendaDocument) -> None:
    _STORE[date] = doc


def docx_exists(date: datetime.date) -> bool:
    return date in _STORE
