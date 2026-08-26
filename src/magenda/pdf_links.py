"""Post-process a rendered agenda PDF to add internal navigation links.

Why post-process the PDF rather than bake OOXML hyperlinks into the docx:
this docx goes through headless LibreOffice to become a PDF (see
tools.render), and there's no guarantee that path preserves internal
w:hyperlink/w:bookmarkStart cross-references as clickable PDF links —
Office-family PDF exporters are known to drop internal navigation links on
some export paths. Writing the link annotations directly onto the already-
rendered PDF sidesteps that entirely, and — since every pass below takes
the doc's XML tree as the source of truth for *what* to link and *where*
to land — still stays deterministic with the rest of this codebase's
docx-is-the-only-state design.

Three link passes:
  - a page-1 daily-schedule entry that names a meeting links to that
    meeting's notes page (add_meeting_links).
  - the "<< Overview" label in every page's header (word/header1.xml,
    repeated on every page) links back to page 0 (add_overview_links).
  - the "Notes >>" label in every page's header links to the closing
    "Further notes" page — always the PDF's last page, recomputed fresh on
    every render, so it still lands correctly no matter how many meeting
    pages were added or removed since the last render (add_notes_links).
A page's own header labels are skipped rather than self-linked (e.g. the
overview page doesn't get a link to itself).
"""
from __future__ import annotations

import pymupdf
from lxml import etree

from magenda import xml_ops


def _insert_goto(page: pymupdf.Page, rect: pymupdf.Rect, target_page: int) -> None:
    page.insert_link(
        {
            "kind": pymupdf.LINK_GOTO,
            "from": rect,
            "page": target_page,
            "to": pymupdf.Point(0, 0),
        }
    )


def add_meeting_links(pdf: pymupdf.Document, body: etree._Element) -> None:
    """Add a GoTo link over each page-1 daily schedule entry that names a
    meeting (per xml_ops.match_schedule_to_meetings), jumping to that
    meeting's notes page. An entry is skipped, rather than guessed at, if
    its text isn't found on the rendered page exactly once (e.g. it also
    happens to appear elsewhere on page 1) or its target page doesn't exist
    in the rendered PDF."""
    pairs = xml_ops.match_schedule_to_meetings(body)
    if not pairs:
        return
    page0 = pdf[0]
    for text, meeting_index in pairs:
        hits = page0.search_for(text)
        if len(hits) != 1:
            continue
        target_page = xml_ops.meeting_page_index(body, meeting_index)
        if not (0 <= target_page < len(pdf)):
            continue
        _insert_goto(page0, hits[0], target_page)


def add_overview_links(pdf: pymupdf.Document) -> None:
    """Add a GoTo link to page 0 over the "<< Overview" header label on
    every other page. Skipped on a page where the label isn't found exactly
    once."""
    for page in pdf[1:]:
        hits = page.search_for(xml_ops.OVERVIEW_LINK_LABEL)
        if len(hits) == 1:
            _insert_goto(page, hits[0], 0)


def add_notes_links(pdf: pymupdf.Document) -> None:
    """Add a GoTo link to the closing 'Further notes' page — always the
    PDF's last page — over the "Notes >>" header label on every other page.
    Skipped on a page where the label isn't found exactly once."""
    last_page = len(pdf) - 1
    for page in pdf[:last_page]:
        hits = page.search_for(xml_ops.NOTES_LINK_LABEL)
        if len(hits) == 1:
            _insert_goto(page, hits[0], last_page)


def add_navigation_links(pdf_bytes: bytes, body: etree._Element) -> bytes:
    """Add every internal navigation link this module knows about to
    `pdf_bytes` and return the result."""
    pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        add_meeting_links(pdf, body)
        add_overview_links(pdf)
        add_notes_links(pdf)
        return pdf.tobytes()
    finally:
        pdf.close()
