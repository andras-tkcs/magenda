"""Add internal navigation links to a rendered agenda PDF.

Three link passes:
  - a page-1 daily-schedule entry that names a meeting links to that
    meeting's notes page (add_meeting_links).
  - the "<< Overview" label in every page's header (repeated on every
    page -- see pdf_assembler.py's header pass) links back to page 0
    (add_overview_links).
  - the "Notes >>" label in every page's header links to the closing
    "Further notes" page -- always the PDF's last page, recomputed fresh
    on every render (add_notes_links).
A page's own header labels are skipped rather than self-linked (e.g. the
overview page doesn't get a link to itself).

Unchanged by the LibreOffice-removal rewrite: this module only ever
operated on the already-assembled PDF via pymupdf search_for/insert_link,
independent of how that PDF was produced.
"""
from __future__ import annotations

import pymupdf

from magenda import layout_constants as LC
from magenda.agenda_state import AgendaState


def _insert_goto(page: pymupdf.Page, rect: pymupdf.Rect, target_page: int) -> None:
    page.insert_link(
        {
            "kind": pymupdf.LINK_GOTO,
            "from": rect,
            "page": target_page,
            "to": pymupdf.Point(0, 0),
        }
    )


def add_meeting_links(pdf: pymupdf.Document, state: AgendaState) -> None:
    """Add a GoTo link over each page-1 daily schedule entry that names a
    meeting (per AgendaState.match_schedule_to_meetings), jumping to that
    meeting's notes page. An entry is skipped, rather than guessed at, if
    its text isn't found on the rendered page exactly once (e.g. it also
    happens to appear elsewhere on page 1) or its target page doesn't exist
    in the rendered PDF."""
    pairs = state.match_schedule_to_meetings()
    if not pairs:
        return
    page0 = pdf[0]
    for text, meeting_index in pairs:
        hits = page0.search_for(text)
        if len(hits) != 1:
            continue
        target_page = state.meeting_page_index(meeting_index)
        if not (0 <= target_page < len(pdf)):
            continue
        _insert_goto(page0, hits[0], target_page)


def add_overview_links(pdf: pymupdf.Document) -> None:
    """Add a GoTo link to page 0 over the "<< Overview" header label on
    every other page. Skipped on a page where the label isn't found exactly
    once."""
    for page in pdf[1:]:
        hits = page.search_for(LC.OVERVIEW_LINK_LABEL)
        if len(hits) == 1:
            _insert_goto(page, hits[0], 0)


def add_notes_links(pdf: pymupdf.Document) -> None:
    """Add a GoTo link to the closing 'Further notes' page — always the
    PDF's last page — over the "Notes >>" header label on every other page.
    Skipped on a page where the label isn't found exactly once."""
    last_page = len(pdf) - 1
    for page in pdf[:last_page]:
        hits = page.search_for(LC.NOTES_LINK_LABEL)
        if len(hits) == 1:
            _insert_goto(page, hits[0], last_page)


def add_navigation_links(pdf_bytes: bytes, state: AgendaState) -> bytes:
    """Add every internal navigation link this module knows about to
    `pdf_bytes` and return the result."""
    pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        add_meeting_links(pdf, state)
        add_overview_links(pdf)
        add_notes_links(pdf)
        return pdf.tobytes()
    finally:
        pdf.close()
