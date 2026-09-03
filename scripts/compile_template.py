#!/usr/bin/env python3
"""One-off template compiler: assets/template.docx -> assets/compiled/.

Run this by hand (needs a local LibreOffice install), and commit its
output, whenever assets/template.docx's *structure* changes (a new slot,
a moved/resized table, a new page type) -- never automatically, never as
part of a build. See docs/design/remove-libreoffice-runtime-dependency.md
and README.md's "Look and feel"/"Building the MCPB extension" sections.

    python scripts/compile_template.py

What it does, in order:

1. Builds several small docx fixtures from assets/template.docx (via
   scripts/compiler/{docx_document,xml_ops}.py -- the same OOXML-editing
   code the runtime used before this rewrite, now compiler-only), with
   every themable slot -- static labels included -- temporarily holding a
   unique sentinel token.
2. Renders each fixture through headless LibreOffice exactly once
   (scripts/compiler/soffice.py).
3. Locates every sentinel in the rendered PDF (pymupdf search_for) to
   capture its exact rect, then redacts (erases) it, leaving a page with
   all the template's non-text chrome (borders, shading, ruled lines) but
   no themable glyphs at all -- see slot_schema.py's module docstring for
   why nothing themable is ever baked in.
4. Writes assets/compiled/chrome.pdf (the redacted page shells) and
   assets/compiled/slots.json (the geometry manifest), plus
   assets/compiled/template.docx.sha256 so CI can detect a template edit
   that was never recompiled.

Every rect ends up in PDF points (72/inch), top-left origin, matching
pymupdf's own coordinate system -- that's what pdf_assembler.py expects.

Known, deliberate scope cut: a handful of small static labels that use one
of the template's 5 Outfit weight names but are never touched by any tool
-- the "NEXT FOUR WEEKS:" heading, the schedule table's own "8am".."6pm"
hour-label column, the to-do checkbox glyph (Wingdings, not Outfit, never
themed even in the pre-rewrite implementation) -- are left baked into
chrome.pdf as-is rather than turned into slots. They render correctly, but
won't pick up a font-pack change the way every other label does. Turning
one into a slot is mechanical (see e.g. "todo.label"/"schedule.label" a few
lines below for the pattern) if this ever needs closing.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pymupdf  # noqa: E402

from magenda import calendar_math, layout_constants as LC  # noqa: E402
from magenda.font_packs import FONT_PACKS  # noqa: E402
from magenda.paths import COMPILED_DIR, TEMPLATE_PATH  # noqa: E402
from magenda.slot_schema import CompiledManifest, DelegatedGeometry, Slot  # noqa: E402

from compiler import font_setup, xml_ops  # noqa: E402
from compiler.docx_document import AgendaDocument, fresh_from_template  # noqa: E402
from compiler.soffice import find_soffice  # noqa: E402

OUTFIT = FONT_PACKS["outfit"]["weights"]  # weight bucket -> "Outfit Black" etc.


def _set_only_text(container, text: str) -> None:
    """Set `container`'s (a <w:p> or an ancestor of one) first <w:t> to
    `text` and blank every other one. A label like "TO-DO LIST " can be
    split across more than one run in the template's own XML (LibreOffice
    is free to do this on save) -- overwriting only the first text node
    and leaving the rest leaves visible fragments of the original text
    behind after this node's sentinel gets redacted."""
    texts = container.findall(".//w:t", xml_ops.NS)
    if not texts:
        raise SystemExit("no <w:t> text node found to replace")
    texts[0].text = text
    for extra in texts[1:]:
        extra.text = ""


# The template's own baked-in sample header values (assets/template.docx,
# transcribed by hand -- see the module probes in this script's git
# history). Used only to re-locate the header on a page that never had its
# header sentinel-filled (every fixture but "header" itself): redacting by
# reusing the header fixture's own sentinel-derived rects doesn't work for
# these specific fields because their sentinel text ("63 78", a 2-digit
# calendar-day placeholder pair) and the real sample text ("19 TUESDAY")
# aren't the same width, so a redaction sized to the sentinel leaves a
# visible fragment of the wider real text beyond it. The narrow calendar-
# grid fields (day-of-month numbers, 3-letter weekday abbreviations, "CW
# nn") don't have this problem -- real and sentinel content there are the
# same length -- so those still reuse the sentinel-derived rect directly.
_REAL_HEADING = "19 TUESDAY"
_REAL_MONTH = "MAY"
_REAL_YEAR = "2026"
_REAL_OVERVIEW = xml_ops.OVERVIEW_LINK_LABEL
_REAL_NOTES = xml_ops.NOTES_LINK_LABEL

# Same reasoning, applied to the "overview" chrome page specifically: its
# to-do/schedule content is redacted using rects captured on a *different*
# render (build_todo_schedule_fixture's own page -- see main()), where
# these two section labels are short numeric tokens, not the real 11/14-
# character label text. Every other todo/schedule slot (task/due column
# headers, the 18+22 body cells) is blank in an untouched template, so
# there's nothing real to leak there -- only these two need real-text
# re-search.
_REAL_TODO_LABEL = "TO-DO LIST"
_REAL_SCHEDULE_LABEL = "DAILY SCHEDULE"

# Redacting with a plain white fill (pymupdf's add_redact_annot default)
# leaves a visible white patch wherever the underlying cell is actually
# shaded -- the calendar header's weekday-name/day-number row (word/
# header1.xml, w:shd fill="E6E6E6") and the "TO-DO LIST"/"DAILY SCHEDULE"/
# delegated-column-header cells (fill="D9D9D9") all are. Every redaction
# below picks the fill that matches what's actually behind the text it's
# removing, transcribed by hand from the template's own w:shd values.
WHITE = (1, 1, 1)
_GRAY_E6 = tuple(int(h, 16) / 255 for h in ("e6", "e6", "e6"))
_GRAY_D9 = tuple(int(h, 16) / 255 for h in ("d9", "d9", "d9"))

# slot id (exact, or dotted-prefix) -> its cell's real background fill.
_SLOT_BG_FILL = {
    "header.weekday_label.": _GRAY_E6,
    "header.dayno.": _GRAY_E6,
    "header.overview_label": _GRAY_E6,
    "header.notes_label": _GRAY_E6,
    "todo.label": _GRAY_D9,
    "schedule.label": _GRAY_D9,
    "delegated.header.task": _GRAY_D9,
    "delegated.header.owner": _GRAY_D9,
    "delegated.header.status": _GRAY_D9,
    # next-four-weeks grid's own M/T/W/T/F/S/S column-header row -- same
    # w:fill="E6E6E6" as the calendar header's weekday-label row above,
    # transcribed from assets/template.docx (missing here left it
    # defaulting to WHITE, the fallback for anything not in this table).
    "next4weeks.col_header.": _GRAY_E6,
}


def _bg_fill(slot_id: str) -> tuple:
    for key, fill in _SLOT_BG_FILL.items():
        if slot_id == key or (key.endswith(".") and slot_id.startswith(key)):
            return fill
    return WHITE


def _real_text_rect(page: pymupdf.Page, text: str) -> pymupdf.Rect:
    hits = page.search_for(text)
    if len(hits) != 1:
        raise SystemExit(f"expected exactly 1 hit for real header text {text!r} on page {page.number}, got {len(hits)}")
    return hits[0]


def _header_redact_rects(page: pymupdf.Page, header_slots: list) -> list:
    """(rect, fill) pairs to blank `page`'s (real, untouched) header
    content with, combining reused sentinel-derived rects (same-width
    fields) with freshly re-searched real-text rects (variable-width
    fields) -- see the module comment above."""
    by_id = {s.id: s.rect for s in header_slots}
    pairs = [
        (rect, _bg_fill(slot_id)) for slot_id, rect in by_id.items()
        if slot_id in ("header.cw",) or slot_id.startswith("header.weekday_label.") or slot_id.startswith("header.dayno.")
    ]
    pairs.append((_real_text_rect(page, _REAL_HEADING), WHITE))
    pairs.append((_real_text_rect(page, _REAL_MONTH), WHITE))
    pairs.append((_real_text_rect(page, _REAL_YEAR), WHITE))
    pairs.append((_real_text_rect(page, _REAL_OVERVIEW), _GRAY_E6))
    pairs.append((_real_text_rect(page, _REAL_NOTES), _GRAY_E6))
    return pairs


def _redact_rects(page: pymupdf.Page, rects, fill: tuple = WHITE) -> None:
    """Blank every rect in `rects` on `page` (text only, chrome around it
    untouched) -- used to strip content whose geometry was already
    captured on a *different* render of this same fixed page layout (see
    build_header_fixture's docstring: only one fixture can own the narrow-
    cell token budget at a time, so every other fixture's chrome page still
    carries this fixture's own real, unredacted header/n4w/todo/schedule
    content and needs it blanked by rect instead of by a fresh search).
    `rects` is either a flat list of rects (all filled with `fill`) or a
    list of (rect, fill) pairs (each filled with its own).

    Every one of these slots sits close enough to a shaded row's own ruled
    border that a naive redact-and-repaint visibly damages it -- see
    _restore_borders_near, which every caller of this function is expected
    to run afterward over the same region. That split (redact+repaint
    here, border restoration as a separate explicit step) is deliberate:
    this function has no reliable way to know which borders "belong" to
    which rect, so it always may cover them a little, and the caller
    -- which knows the row/page layout -- puts them back."""
    items = [item if isinstance(item, tuple) else (item, fill) for item in rects]
    if not items:
        return
    # Redact with plain white; apply_redactions with graphics=NONE so a
    # rect sitting right against a vector border doesn't clip it (the
    # default, REMOVE_IF_COVERED, clips any path it merely intersects, not
    # just ones it fully covers).
    for rect, _ in items:
        page.add_redact_annot(pymupdf.Rect(rect), fill=WHITE)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    # Non-white backgrounds are painted back in as a second, separate step
    # (add_redact_annot's own fill also draws a same-color stroke around
    # the rect, and its corner/edge antialiasing doesn't blend seamlessly
    # with a plain draw_rect fill of the same color placed right next to
    # it -- painting fill-only, no stroke, directly over the redacted area
    # avoids that). Outset a little so this fill's own edge lands inside
    # the surrounding same-color region rather than exactly on the
    # redaction's edge -- gray blended with gray disappears; gray blended
    # with the white underneath doesn't. This routinely bleeds into a
    # nearby ruled border's own stroke width (most of these rects sit
    # within a point or two of one) -- expected, left for
    # _restore_borders_near to fix up afterward, not worked around here.
    _BLEED = 2.0
    for rect, item_fill in items:
        if item_fill != WHITE:
            r0 = pymupdf.Rect(rect)
            r = pymupdf.Rect(r0.x0 - _BLEED, r0.y0 - _BLEED, r0.x1 + _BLEED, r0.y1 + _BLEED)
            page.draw_rect(r, color=None, fill=item_fill)


def _restore_borders_near(page: pymupdf.Page, y0: float, y1: float, min_width: float = 50) -> None:
    """Redraw every horizontal ruled line at least `min_width` points wide
    whose y falls in [y0, y1] (queried fresh from the page's own vector
    content, not hardcoded) as the topmost content on the page. A stroke
    has thickness -- the template's ruled borders are 3pt wide -- so a
    label sitting close to one, as most do, has its capture rect
    overlapping the stroke's own width, not just the hairline path down
    its center; the redact/repaint _redact_rects does for that label then
    visibly eats into the border wherever they overlap, however small the
    redacted rect. Restoring the exact same strokes on top afterward is
    simpler and more robust than trying to keep every label's geometry
    clear of every border it might be near.

    `min_width` defaults to filtering out short vector paths that
    happen to be horizontal but aren't a ruled line this function should
    touch (e.g. a checkbox's own stroke) -- pass a lower value for a
    narrower decorative rule (e.g. the to-do list's "Task"/"Due" column-
    header underlines, ~23pt wide) that a caller wants restored too."""
    for d in page.get_drawings():
        if d["type"] != "s":
            continue
        r = d["rect"]
        if r.height > 0.5 or r.width < min_width:
            continue  # not a wide-enough horizontal ruled line
        if not (y0 <= r.y0 <= y1):
            continue
        page.draw_line((r.x0, r.y0), (r.x1, r.y1), color=d.get("color") or (0, 0, 0),
                        width=d.get("width") or 1)


def render_to_pdf(doc: AgendaDocument, soffice: str, tmp: Path, name: str) -> pymupdf.Document:
    docx_path = tmp / f"{name}.docx"
    doc.save(docx_path)
    result = subprocess.run(
        [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(tmp), str(docx_path)],
        capture_output=True, text=True, timeout=120,
    )
    pdf_path = tmp / f"{name}.pdf"
    if result.returncode != 0 or not pdf_path.exists():
        raise SystemExit(f"LibreOffice failed to render {name}.docx: {result.stderr or result.stdout}")
    return pymupdf.open(str(pdf_path))


class SlotCollector:
    """Accumulates (sentinel -> Slot-without-rect) registrations before a
    render, then resolves every sentinel to a rect afterward (search_for)
    and redacts it from the page -- shared logic for every fixture this
    script renders."""

    def __init__(self, digits: int = 3, start: int | None = None):
        self._pending: dict[str, dict] = {}
        self._counter = 0
        # Fixed-width, digits-only numeral, sized per fixture. Digit glyphs
        # measure noticeably narrower than letters at the same size
        # (checked with text_fit.text_width_twips while developing this
        # script), and even so the template's narrowest cells -- the
        # calendar header's day-number columns, the next-four-weeks day
        # cells, both well under an inch wide -- only have room for 2
        # digits before wrapping onto a second line, which would throw off
        # the very geometry this script is trying to capture. Fixtures
        # touching only roomier cells use 3 digits for a bigger namespace.
        # Fixed-width so no token is ever a prefix of another, which would
        # make search_for over- or under-match. `start` shifts the range
        # clear of real static digits already on the page that a fixed-
        # width search could otherwise match a substring of (e.g. the
        # schedule table's own "8am".."12pm" hour labels overlap 8-12).
        self._base = start if start is not None else 10 ** (digits - 1)

    def token(self) -> str:
        self._counter += 1
        return str(self._base + self._counter)

    def register(self, sentinel: str, *, id: str, role: str, weight: str, size_half_points: int,
                 align: str = "left", text: str | None = None) -> None:
        self._pending[sentinel] = dict(id=id, role=role, weight=weight, size_half_points=size_half_points,
                                        align=align, text=text)

    def resolve(self, page: pymupdf.Page) -> list[Slot]:
        """For every registered sentinel found on `page`: capture its rect,
        redact it (blanking the glyphs but keeping surrounding chrome),
        and return the finished Slot list. Redaction itself is one batched
        call via _redact_rects (see its docstring for why a non-white fill
        goes through a second, separate paint step there)."""
        slots = []
        pairs = []
        for sentinel, meta in self._pending.items():
            hits = page.search_for(sentinel)
            if len(hits) != 1:
                raise SystemExit(f"sentinel {sentinel!r} ({meta['id']}) found {len(hits)} times on page "
                                  f"{page.number}, expected exactly 1")
            rect = hits[0]
            slots.append(Slot(id=meta["id"], rect=(rect.x0, rect.y0, rect.x1, rect.y1), role=meta["role"],
                               weight=meta["weight"], size_half_points=meta["size_half_points"],
                               align=meta["align"], text=meta["text"]))
            pairs.append((rect, _bg_fill(meta["id"])))
        _redact_rects(page, pairs)
        return slots


# ---------------------------------------------------------------------------
# Fixture 1a: "header" -- the calendar header block (repeats on every
# page), the weekday-label/day-number rows, the overview/notes nav labels,
# and the next-four-weeks grid. Kept in its own fixture (and its own
# SlotCollector(digits=2)) because every field here lives in a cell under
# an inch wide -- see SlotCollector's docstring -- so it needs the whole
# narrow 2-digit token budget to itself, with no risk of a wider token from
# an unrelated region (the to-do list, the daily schedule) sharing the page
# and colliding as a substring.
# ---------------------------------------------------------------------------

def build_header_fixture(sc: SlotCollector) -> AgendaDocument:
    doc = fresh_from_template()
    body = doc.body

    # -- calendar header block (word header, repeats on every page) --
    block = xml_ops.find_calendar_block(doc.header)
    day_tok, wd_tok, cw_tok, mo_tok, yr_tok = (sc.token() for _ in range(5))
    xml_ops.apply_calendar_block(block, {
        "day": day_tok, "weekday_name": wd_tok, "cw": f"CW {cw_tok}", "month": mo_tok, "year": yr_tok,
        "week_days": [1, 2, 3, 4, 5, 6, 7],  # placeholder; day-number row captured separately below
    })
    # day_tok/wd_tok share one run/style ("19 TUESDAY") but live in separate
    # <w:t> nodes -- search_for on the two tokens joined by a space can
    # spuriously double-match across line boundaries, so each is captured
    # as its own fragment and unioned into one "header.heading" slot in
    # main() instead.
    sc.register(day_tok, id="_frag.header.heading.day", role="heading", weight="black",
                size_half_points=LC.HEADING_FONT_SIZE)
    sc.register(wd_tok, id="_frag.header.heading.weekday", role="heading", weight="black",
                size_half_points=LC.HEADING_FONT_SIZE)
    sc.register(f"CW {cw_tok}", id="header.cw", role="heading", weight="regular",
                size_half_points=LC.HEADING_SUB_FONT_SIZE)
    sc.register(mo_tok, id="header.month", role="heading", weight="regular", size_half_points=LC.HEADING_SUB_FONT_SIZE)
    sc.register(yr_tok, id="header.year", role="heading", weight="regular", size_half_points=LC.HEADING_SUB_FONT_SIZE)

    # weekday-label row (MON..SUN, static) and day-number row (dynamic) --
    # both live in block.dayno_row's siblings; weekday labels are the row
    # right above dayno_row (see find_calendar_block: title_row, then the
    # weekday row, then dayno_row two rows down).
    weekday_row = block.title_row.getnext()
    weekday_cells = weekday_row.findall("w:tc", xml_ops.NS)
    dayno_cells = block.dayno_row.findall("w:tc", xml_ops.NS)
    weekday_names = calendar_math.WEEKDAY_NAME
    for i in range(7):
        role = "weekend" if i in calendar_math.WEEKEND_INDICES else "body"
        tok = sc.token()
        xml_ops.set_cell_text(weekday_cells[i], tok)
        sc.register(tok, id=f"header.weekday_label.{i}", role=role, weight="regular",
                    size_half_points=LC.WEEKDAY_LABEL_FONT_SIZE, text=weekday_names[i][:3])
        tok2 = sc.token()
        xml_ops.set_cell_text(dayno_cells[i], tok2)
        sc.register(tok2, id=f"header.dayno.{i}", role=role, weight="regular",
                    size_half_points=LC.WEEKDAY_LABEL_FONT_SIZE)

    # "<< Overview" / "Notes >>" nav labels: left as real, untouched text
    # (captured by real-text search in main(), same reasoning as
    # build_meeting_fixture's "Meeting title: " label) rather than
    # sentinel-replaced -- "Notes >>" sits after a tab that lands relative
    # to "<< Overview"'s own rendered width, so shortening it to a
    # sentinel would shift where "Notes >>" actually renders.

    # -- next four weeks grid: header letters (static) + 4x8 body cells --
    n4w = xml_ops.find_next_four_weeks_table(body)
    n4w_rows = n4w.findall("w:tr", xml_ops.NS)
    header_cells = n4w_rows[0].findall("w:tc", xml_ops.NS)
    for i in range(7):
        role = "weekend" if i in LC.WEEKEND_COLUMN_INDICES else "body"
        tok = sc.token()
        xml_ops.set_cell_text(header_cells[i + 1], tok)
        sc.register(tok, id=f"next4weeks.col_header.{i}", role=role, weight="thin",
                    size_half_points=LC.NEXT_FOUR_WEEKS_FONT_SIZE, text=LC.WEEKDAY_HEADER_LETTERS[i])
    for wk, row in enumerate(n4w_rows[1:]):
        cells = row.findall("w:tc", xml_ops.NS)
        cw_tok = sc.token()
        xml_ops.set_cell_text(cells[0], cw_tok)
        sc.register(cw_tok, id=f"next4weeks.week.{wk}.cw", role="body", weight="thin",
                    size_half_points=LC.NEXT_FOUR_WEEKS_FONT_SIZE)
        for i in range(7):
            role = "weekend" if i in LC.WEEKEND_COLUMN_INDICES else "body"
            tok = sc.token()
            xml_ops.set_cell_text(cells[i + 1], tok)
            sc.register(tok, id=f"next4weeks.week.{wk}.day.{i}", role=role, weight="thin",
                        size_half_points=LC.NEXT_FOUR_WEEKS_FONT_SIZE)

    return doc


# ---------------------------------------------------------------------------
# Fixture 1b: "todo_schedule" -- the to-do list (label, column headers, 18
# body rows) and the daily schedule (label, 22 notes-cell slots). These
# cells are roomy enough for the default 3-digit token, and since they
# render on the same "overview" chrome page as the header fixture above,
# their slots get merged into page_slots["overview"] in main() -- they're
# only a separate *render* to keep well clear of the header fixture's
# narrow-cell token budget (see build_header_fixture's docstring).
# ---------------------------------------------------------------------------

def build_todo_schedule_fixture(sc: SlotCollector) -> AgendaDocument:
    doc = fresh_from_template()
    body = doc.body

    for p in body.findall(".//w:p", xml_ops.NS):
        txt = "".join(t.text or "" for t in p.findall(".//w:t", xml_ops.NS))
        if txt == "TO-DO LIST ":
            tok = sc.token()
            _set_only_text(p, tok)
            sc.register(tok, id="todo.label", role="label", weight="black", size_half_points=36, text="TO-DO LIST")
        elif txt == "DAILY SCHEDULE":
            tok = sc.token()
            _set_only_text(p, tok)
            sc.register(tok, id="schedule.label", role="label", weight="black", size_half_points=36,
                        text="DAILY SCHEDULE")

    todo_table = xml_ops.find_todo_table(body)
    todo_rows = todo_table.findall("w:tr", xml_ops.NS)
    task_tok = sc.token()
    xml_ops.set_cell_text(todo_rows[0].findall("w:tc", xml_ops.NS)[1], task_tok)
    sc.register(task_tok, id="todo.col.task", role="body", weight="thin", size_half_points=24, text="Task")
    due_tok = sc.token()
    xml_ops.set_cell_text(todo_rows[0].findall("w:tc", xml_ops.NS)[2], due_tok)
    sc.register(due_tok, id="todo.col.due", role="body", weight="thin", size_half_points=24, text="Due")
    for i, row in enumerate(todo_rows[1:]):
        cells = row.findall("w:tc", xml_ops.NS)
        t_tok, d_tok = sc.token(), sc.token()
        xml_ops.set_cell_text(cells[1], t_tok)
        xml_ops.set_cell_text(cells[2], d_tok)
        sc.register(t_tok, id=f"todo.row.{i}.task", role="body", weight="thin", size_half_points=24)
        sc.register(d_tok, id=f"todo.row.{i}.due", role="body", weight="thin", size_half_points=24)
    # The checkbox column (cells[0] of every body row) is deliberately left
    # untouched here -- see main()'s checkbox handling for why (a Wingdings-
    # encoded glyph nothing guarantees is actually installed on whichever
    # machine runs this compiler) and how its rect is derived without
    # needing a sentinel of its own (from the task cell captured above,
    # arithmetically, rather than risking a 3rd token per row overflowing
    # this already-narrow column and throwing off page geometry the way an
    # earlier version of this function did).

    schedule_table = xml_ops.find_schedule_table(body)
    for i, row in enumerate(schedule_table.findall("w:tr", xml_ops.NS)):
        notes_cell = row.findall("w:tc", xml_ops.NS)[1]
        tok = sc.token()
        xml_ops.set_cell_text(notes_cell, tok)
        sc.register(tok, id=f"schedule.slot.{i}", role="body", weight="thin",
                    size_half_points=LC.SCHEDULE_NOTES_FONT_SIZE)

    return doc


# ---------------------------------------------------------------------------
# Fixture 2: "meeting_unit" -- the title slot; ruled notes lines are
# untouched (non-text) chrome.
# ---------------------------------------------------------------------------

def build_meeting_fixture(sc: SlotCollector) -> AgendaDocument:
    """The "Meeting title: " label is deliberately left as real, untouched
    text here (not sentinel-replaced) -- its rect is captured by searching
    that real text directly in main(), not through SlotCollector. Its
    rendered width doesn't reliably match text_fit's PIL-based estimate
    (pdf_assembler.py's runtime "widen for real content" logic assumes it
    does, for slots where that's close enough), and since the title's own
    rect immediately follows it, an overestimate here means drawing the
    label on top of the title. Capturing the label's real rect instead
    sidesteps needing an estimate for it at all."""
    doc = fresh_from_template()
    title_para, _ = xml_ops.find_meeting_unit_template(doc.body)
    tok = sc.token()
    xml_ops.set_meeting_title(title_para, tok)
    sc.register(tok, id="meeting.title", role="accent", weight="extralight",
                size_half_points=LC.MEETING_TITLE_FONT_SIZE)
    return doc


# ---------------------------------------------------------------------------
# Fixture 3: "further_notes" -- just the closing heading.
# ---------------------------------------------------------------------------

def build_further_notes_fixture(sc: SlotCollector) -> AgendaDocument:
    doc = fresh_from_template()
    para = xml_ops.find_further_notes_paragraph(doc.body)
    tok = sc.token()
    _set_only_text(para, tok)
    sc.register(tok, id="further_notes.label", role="notes", weight="extralight",
                size_half_points=LC.FURTHER_NOTES_FONT_SIZE, text=LC.FURTHER_NOTES_TEXT)
    return doc


# ---------------------------------------------------------------------------
# Fixture 4: "delegated_shell" -- header row (label/label/label, static) +
# TWO calibration body rows (1-line task text each) to capture column
# geometry and the base row height. The two body rows are cropped back out
# of the final chrome page -- only the header row chrome is kept, since
# actual body rows are drawn procedurally at runtime (variable count).
# ---------------------------------------------------------------------------

def build_delegated_fixture(sc: SlotCollector) -> AgendaDocument:
    doc = fresh_from_template()
    xml_ops.rebuild_delegated_tasks(doc.body, [
        {"text": "x", "owner": "", "cadence": "daily", "status": "", "marked": False},
        {"text": "x", "owner": "", "cadence": "daily", "status": "", "marked": False},
    ])
    tables = xml_ops.find_delegated_tables(doc.body)
    header_row = tables[0].findall("w:tr", xml_ops.NS)[0]
    header_cells = header_row.findall("w:tc", xml_ops.NS)
    task_tok, owner_tok, status_tok = sc.token(), sc.token(), sc.token()
    xml_ops.set_cell_text(header_cells[1], task_tok)
    xml_ops.set_cell_text(header_cells[2], owner_tok)
    xml_ops.set_cell_text(header_cells[3], status_tok)
    sc.register(task_tok, id="delegated.header.task", role="label", weight="black",
                size_half_points=LC.DELEGATED_HEADER_FONT_SIZE, align="center", text="Task & cadence")
    sc.register(owner_tok, id="delegated.header.owner", role="label", weight="black",
                size_half_points=LC.DELEGATED_HEADER_FONT_SIZE, align="center", text="Owner")
    sc.register(status_tok, id="delegated.header.status", role="label", weight="black",
                size_half_points=LC.DELEGATED_HEADER_FONT_SIZE, align="center", text="Status")

    # footer1.xml's "Notes and updates" heading, used only on this page.
    footer1 = doc._trees.get("word/footer1.xml")
    if footer1 is not None:
        for p in footer1.getroot().findall(".//w:p", xml_ops.NS):
            texts = p.findall(".//w:t", xml_ops.NS)
            if texts and "".join(t.text or "" for t in texts) == "Notes and updates":
                tok = sc.token()
                texts[0].text = tok
                for extra in texts[1:]:
                    extra.text = ""
                sc.register(tok, id="delegated.footer_label", role="accent", weight="extralight",
                            size_half_points=40, text="Notes and updates")
                break

    # Row-1's "Daily" cadence label (row-1's own row) marks where body rows
    # start, and row-2's marks the calibration delta -- both are removed
    # from the final chrome (see main()'s handling of DELEGATED_CALIBRATION_IDS).
    for row_idx, row in enumerate(tables[0].findall("w:tr", xml_ops.NS)[1:]):
        cadence_cell = row.findall("w:tc", xml_ops.NS)[1]
        tok = sc.token()
        xml_ops.set_run_text_at(cadence_cell, 0, tok)
        sc.register(tok, id=f"_calib.row{row_idx}", role="body", weight="extralight", size_half_points=18)
    return doc


def main() -> None:
    soffice = find_soffice()
    font_setup.ensure_fonts_installed()

    with tempfile.TemporaryDirectory(prefix="magenda-compile-") as tmp_str:
        tmp = Path(tmp_str)

        sc_header = SlotCollector(digits=2, start=20)  # 10-12 overlap "8am".."12pm"'s own hour digits
        header_doc = build_header_fixture(sc_header)
        header_pdf = render_to_pdf(header_doc, soffice, tmp, "header")
        header_only_slots = sc_header.resolve(header_pdf[0])
        day_frag = next(s for s in header_only_slots if s.id == "_frag.header.heading.day")
        wd_frag = next(s for s in header_only_slots if s.id == "_frag.header.heading.weekday")
        heading_rect = (
            min(day_frag.rect[0], wd_frag.rect[0]), min(day_frag.rect[1], wd_frag.rect[1]),
            max(day_frag.rect[2], wd_frag.rect[2]), max(day_frag.rect[3], wd_frag.rect[3]),
        )
        heading_slot = Slot(id="header.heading", rect=heading_rect, role="heading", weight="black",
                            size_half_points=LC.HEADING_FONT_SIZE)
        header_slots = [s for s in header_only_slots if not s.id.startswith("_frag.")] + [heading_slot]
        n4w_slots = [s for s in header_slots if not s.id.startswith("header.")]
        header_slots = [s for s in header_slots if s.id.startswith("header.")]

        # "<< Overview" / "Notes >>": real-text search (see
        # build_header_fixture's comment on why these were never
        # sentinel-replaced), redacted the same way.
        overview_rect = _real_text_rect(header_pdf[0], _REAL_OVERVIEW)
        notes_rect = _real_text_rect(header_pdf[0], _REAL_NOTES)
        header_slots += [
            Slot(id="header.overview_label", rect=tuple(overview_rect), role="body", weight="regular",
                 size_half_points=16, text=_REAL_OVERVIEW),
            Slot(id="header.notes_label", rect=tuple(notes_rect), role="body", weight="regular",
                 size_half_points=16, text=_REAL_NOTES),
        ]
        _redact_rects(header_pdf[0], [(overview_rect, _GRAY_E6), (notes_rect, _GRAY_E6)])

        sc_todo_schedule = SlotCollector()
        todo_schedule_doc = build_todo_schedule_fixture(sc_todo_schedule)
        todo_schedule_pdf = render_to_pdf(todo_schedule_doc, soffice, tmp, "todo_schedule")
        todo_schedule_slots = sc_todo_schedule.resolve(todo_schedule_pdf[0])

        # "overview" chrome (see chrome_pages below) is built from the
        # header fixture's page. Its to-do/schedule region hasn't been
        # redacted yet -- that sentinel text lives on a *different* render
        # of this same fixed table layout -- but since neither fixture's
        # own content affects the other's geometry, the todo/schedule
        # rects captured there apply unchanged here too: redact them
        # directly by rect rather than re-searching.
        todo_schedule_redact_pairs = [
            (s.rect, _bg_fill(s.id)) for s in todo_schedule_slots if s.id not in ("todo.label", "schedule.label")
        ]
        todo_schedule_redact_pairs.append((_real_text_rect(header_pdf[0], _REAL_TODO_LABEL), _GRAY_D9))
        todo_schedule_redact_pairs.append((_real_text_rect(header_pdf[0], _REAL_SCHEDULE_LABEL), _GRAY_D9))

        # The 18 checkbox-column cells are real, untouched content on this
        # page (build_todo_schedule_fixture never sentinel-replaces them --
        # see its comment) -- their own literal Wingdings-encoded character
        # (U+00A1), found by search_for like any other real-text redaction
        # here, just with every hit wanted rather than exactly one.
        checkbox_hits = header_pdf[0].search_for("¡")
        if len(checkbox_hits) != LC.TODO_ROW_CAPACITY:
            raise SystemExit(f"expected {LC.TODO_ROW_CAPACITY} checkbox glyphs, found {len(checkbox_hits)}")
        todo_schedule_redact_pairs += [(hit, WHITE) for hit in checkbox_hits]
        _redact_rects(header_pdf[0], todo_schedule_redact_pairs)
        # Covers the calendar header band's own borders (y=38/71), the
        # "TO-DO LIST"/"DAILY SCHEDULE" label boxes' (y=87-113), and every
        # row's own ruled line the length of both tables -- every one of
        # the 18 todo rows and 20 schedule rows just redacted a cell right
        # up against its row's border, and each one bleeds into it exactly
        # like the header labels do -- see _redact_rects/
        # _restore_borders_near's docstrings for why this is a separate,
        # later step rather than something _redact_rects itself avoids
        # needing. A too-narrow y-window here (this used to stop at 120,
        # covering only the header band) previously left almost every row
        # boundary on the page with a small gap right where that row's own
        # dynamic content had been.
        table_bottom = max(s.rect[3] for s in todo_schedule_slots) + 10
        _restore_borders_near(header_pdf[0], 30, table_bottom)
        # The "Task"/"Due" column-header underlines are real ruled lines
        # too, but short (~23pt, one word wide) -- narrower than the
        # min_width filter above lets through by default, since that's
        # tuned to skip incidental short horizontal strokes (e.g. a
        # checkbox's own edge) elsewhere on the page. Restore them
        # separately, scoped tight enough (y=140-146) that a lower
        # min_width here can't pick up anything unintended.
        _restore_borders_near(header_pdf[0], 140, 146, min_width=15)

        # Vector-drawn checkboxes, not text: nothing guarantees Wingdings --
        # or any specific symbol font -- is installed on whichever machine
        # happens to run this compiler (it wasn't, developing this
        # script -- LibreOffice silently substituted a fallback font, so
        # the glyph just redacted above rendered as "¡", not a checkbox).
        # A plain square outline, baked into chrome.pdf as chrome (never
        # touched at runtime, same as the ruled lines it sits next to),
        # renders identically everywhere by construction instead of
        # gambling on a font substitution nobody controls.
        for hit in checkbox_hits:
            size = min(hit.width, hit.height) * 0.9
            cx, cy = hit.x0 + hit.width / 2, (hit.y0 + hit.y1) / 2
            box = pymupdf.Rect(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2)
            header_pdf[0].draw_rect(box, color=(0, 0, 0), fill=None, width=0.75)

        overview_pdf = header_pdf
        overview_page_slots = n4w_slots + todo_schedule_slots

        # Every fixture built from fresh_from_template() carries the full
        # page sequence (0=overview, 1=meeting, [2=closing or more meeting
        # pages]), not just the one page each fixture happens to sentinel --
        # only page 0 is "overview"; the meeting/closing content each
        # fixture actually targets lands on page 1 / the last page.
        # Every physical page carries its own independent copy of the
        # header's content (a PDF page has no shared "header part" the way
        # a docx section does) -- captured once, from header_pdf[0], but
        # needing this same redaction on every *other* chrome page too, or
        # its real (unsentineled, and never fully identical in geometry to
        # the sentinel-filled capture -- see _header_redact_rects) header
        # content leaks straight into the compiled bundle.
        sc_meeting = SlotCollector()
        meeting_doc = build_meeting_fixture(sc_meeting)
        meeting_pdf = render_to_pdf(meeting_doc, soffice, tmp, "meeting")
        meeting_slots = sc_meeting.resolve(meeting_pdf[1])
        meeting_label_rect = _real_text_rect(meeting_pdf[1], LC.MEETING_TITLE_PREFIX.rstrip())
        meeting_slots.append(Slot(id="meeting.label", rect=tuple(meeting_label_rect), role="accent",
                                  weight="extralight", size_half_points=LC.MEETING_TITLE_FONT_SIZE,
                                  text=LC.MEETING_TITLE_PREFIX.rstrip()))
        _redact_rects(meeting_pdf[1], [meeting_label_rect])
        _redact_rects(meeting_pdf[1], _header_redact_rects(meeting_pdf[1], header_slots))
        _restore_borders_near(meeting_pdf[1], 30, 120)

        sc_notes = SlotCollector()
        notes_doc = build_further_notes_fixture(sc_notes)
        notes_pdf = render_to_pdf(notes_doc, soffice, tmp, "further_notes")
        notes_page = notes_pdf[len(notes_pdf) - 1]
        notes_slots = sc_notes.resolve(notes_page)
        _redact_rects(notes_page, _header_redact_rects(notes_page, header_slots))
        _restore_borders_near(notes_page, 30, 120)

        sc_delegated = SlotCollector()
        delegated_doc = build_delegated_fixture(sc_delegated)
        delegated_pdf = render_to_pdf(delegated_doc, soffice, tmp, "delegated")
        # Page order for this fixture: 0=overview, 1=delegated (its 2
        # calibration rows fit on one page), 2=blank meeting slot, 3=closing.
        delegated_page = delegated_pdf[1]
        all_delegated_slots = sc_delegated.resolve(delegated_page)
        _restore_borders_near(delegated_page, 100, 135)  # the delegated table's own header-row borders
        delegated_header_slots = [s for s in all_delegated_slots if not s.id.startswith("_calib.")]
        calib = {s.id: s for s in all_delegated_slots if s.id.startswith("_calib.")}
        # The header labels are center-aligned within their own cell (see
        # xml_ops._build_delegated_table_shell's jc="center"), so a label's
        # own rect.x0 is *not* its cell's left edge -- derive the cell's
        # actual center from the captured rect instead, then work outward
        # by each column's known width (layout_constants.py, transcribed
        # from the template) to the table's true top-left corner.
        task_slot = next(s for s in delegated_header_slots if s.id == "delegated.header.task")
        task_center_x = (task_slot.rect[0] + task_slot.rect[2]) / 2
        task_cell_left = task_center_x - (LC.DELEGATED_COLUMN_WIDTHS_TWIPS["task"] / 20) / 2
        row0_top = calib["_calib.row0"].rect[1]
        row1_top = calib["_calib.row1"].rect[1]
        row_overhead_twips = (row1_top - row0_top) * 20  # points -> twips
        # table_top_left.y is where the runtime draws row 0's *border*, not
        # its text -- back that out from row 0's cadence-label top (the
        # calibration render's own ground truth) by the same "spacing
        # before" every delegated cell's paragraph carries (see
        # xml_ops._delegated_cell's w:spacing w:before="240" -- 12pt).
        # Using the header labels' own position here instead (as an
        # earlier version of this script did) put row 0 at the *header's*
        # row, not below it -- row 0's content then drew right on top of
        # the header labels.
        _ROW_SPACING_BEFORE_PT = 240 / 20
        table_top_left = (
            task_cell_left - LC.DELEGATED_COLUMN_WIDTHS_TWIPS["number"] / 20,
            row0_top - _ROW_SPACING_BEFORE_PT,
        )
        _redact_rects(delegated_page, _header_redact_rects(delegated_page, header_slots))
        _restore_borders_near(delegated_page, 30, 120)  # the calendar header band's own borders
        # The two calibration rows themselves (row number, "x" task text,
        # and their borders/shading) must not survive into chrome -- actual
        # body rows are drawn procedurally at runtime (pdf_assembler.py),
        # variable in count, so the chrome page carries the header row
        # only. Blank the whole two-row band, not just its sentinel text:
        # a plain redaction only strips text, leaving borders/fill behind.
        calib_top = min(s.rect[1] for s in calib.values()) - 4
        calib_bottom = max(s.rect[3] for s in calib.values()) + 12
        table_left = table_top_left[0]
        table_width_pt = sum(LC.DELEGATED_COLUMN_WIDTHS_TWIPS.values()) / 20
        # add_redact_annot (not draw_rect): draw_rect only paints over the
        # glyphs/borders visually -- the underlying text and vector objects
        # stay in the page's content stream and would still turn up in a
        # later get_text()/search_for (pdf_links.py's navigation-link
        # search included). Redaction actually removes covered content.
        delegated_page.add_redact_annot(
            pymupdf.Rect(table_left, calib_top, table_left + table_width_pt, calib_bottom), fill=(1, 1, 1),
        )
        delegated_page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        # -- assemble chrome.pdf: one page per role, in a fixed order --
        chrome = pymupdf.open()
        chrome.insert_pdf(overview_pdf, from_page=0, to_page=0)
        chrome.insert_pdf(delegated_pdf, from_page=1, to_page=1)
        chrome.insert_pdf(meeting_pdf, from_page=1, to_page=1)
        chrome.insert_pdf(notes_pdf, from_page=len(notes_pdf) - 1, to_page=len(notes_pdf) - 1)
        chrome_pages = {"overview": 0, "delegated_shell": 1, "meeting_unit": 2, "further_notes": 3}

        page0 = overview_pdf[0]
        manifest = CompiledManifest(
            template_docx_sha256=hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest(),
            page_width=page0.rect.width,
            page_height=page0.rect.height,
            chrome_pages=chrome_pages,
            header_slots=header_slots,
            page_slots={
                "overview": overview_page_slots,
                "meeting_unit": meeting_slots,
                "further_notes": notes_slots,
                "delegated_shell": delegated_header_slots,
            },
            delegated=DelegatedGeometry(table_top_left=table_top_left, row_overhead_twips=row_overhead_twips),
        )

        COMPILED_DIR.mkdir(parents=True, exist_ok=True)
        (COMPILED_DIR / "chrome.pdf").write_bytes(chrome.tobytes())
        (COMPILED_DIR / "slots.json").write_text(json.dumps(manifest.to_dict(), indent=2))
        (COMPILED_DIR / "template.docx.sha256").write_text(manifest.template_docx_sha256 + "\n")
        chrome.close()

        for d in (overview_pdf, meeting_pdf, notes_pdf, delegated_pdf):
            d.close()

    total_slots = len(header_slots) + sum(len(v) for v in manifest.page_slots.values())
    print(f"wrote {COMPILED_DIR} ({total_slots} slots, {len(chrome_pages)} chrome pages, "
          f"row_overhead={row_overhead_twips:.0f}tw)")


if __name__ == "__main__":
    main()
