"""Low-level OOXML manipulation for the agenda template.

Every function here is a pure tree edit: locate a node by a stable structural
signature (font/color/text fingerprint baked into the template), then set
text or splice a cloned subtree. No node is ever authored from scratch with
free-form formatting — all formatting is copied from the template's own runs.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from lxml import etree

from magenda.text_fit import fit_downsize_or_wrap, fit_single_line, text_width_twips

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def qn(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{W}}}{local}"


class MagendaError(Exception):
    """Raised for caller-facing errors (capacity exceeded, missing doc, etc)."""


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------

def _runs(el: etree._Element) -> list[etree._Element]:
    return el.findall(".//w:r", NS)


def set_cell_text(tc: etree._Element, text: str) -> None:
    """Collapse a table cell's first paragraph to a single run with `text`,
    preserving that first run's formatting (rPr). Any extra runs (split-run
    artifacts, or leftover runs from a longer placeholder) are removed."""
    p = tc.find("w:p", NS)
    if p is None:
        raise MagendaError("cell has no paragraph to write into")
    runs = p.findall("w:r", NS)
    if not runs:
        # No run exists yet (fully empty cell) — the template still carries
        # its intended formatting on the paragraph mark (w:pPr/w:rPr), e.g.
        # an empty to-do row stores its "Outfit Thin" font there since there
        # was never any text to attach a run to. Copy it onto the new run so
        # injected text doesn't fall back to the document's default font.
        r = etree.SubElement(p, qn("w:r"))
        mark_rpr = p.find("w:pPr/w:rPr", NS)
        if mark_rpr is not None:
            r.append(copy.deepcopy(mark_rpr))
        t = etree.SubElement(r, qn("w:t"))
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = text
        return
    first = runs[0]
    for extra in runs[1:]:
        p.remove(extra)
    t = first.find("w:t", NS)
    if t is None:
        t = etree.SubElement(first, qn("w:t"))
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text


def cell_text(tc: etree._Element) -> str:
    return "".join(t.text or "" for t in tc.findall(".//w:t", NS))


def cell_run_font(tc: etree._Element) -> tuple[str, int]:
    """(font family, size in half-points) that text typed into this (possibly
    empty) cell would inherit, per the same resolution order as set_cell_text:
    an existing run's rPr, else the paragraph mark's rPr, else the document
    default (Outfit, 12pt)."""
    p = tc.find("w:p", NS)
    rpr = None
    if p is not None:
        runs = p.findall("w:r", NS)
        rpr = runs[0].find("w:rPr", NS) if runs else p.find("w:pPr/w:rPr", NS)
    font_el = rpr.find("w:rFonts", NS) if rpr is not None else None
    sz_el = rpr.find("w:sz", NS) if rpr is not None else None
    family = font_el.get(qn("w:ascii")) if font_el is not None and font_el.get(qn("w:ascii")) else "Outfit"
    size = int(sz_el.get(qn("w:val"))) if sz_el is not None else 24
    return family, size


def cell_width_twips(tc: etree._Element) -> int:
    tcW = tc.find("w:tcPr/w:tcW", NS)
    if tcW is None:
        raise MagendaError("cell has no explicit width to measure against")
    return int(tcW.get(qn("w:w")))


# None of this template's cells set an explicit w:tblCellMar/w:tcMar, so Word
# and LibreOffice both fall back to the standard default left/right cell
# margin of 108 twips each — text-fitting has to budget for that or it
# computes a line as "fits" when it actually wraps/overflows once rendered.
DEFAULT_CELL_MARGIN_TWIPS = 108


def cell_text_width_twips(tc: etree._Element) -> int:
    """Usable width for text fitting inside a cell: its w:tcW minus the
    default left/right cell margins (see DEFAULT_CELL_MARGIN_TWIPS)."""
    return cell_width_twips(tc) - 2 * DEFAULT_CELL_MARGIN_TWIPS


def set_run_size(tc: etree._Element, size_half_points: int) -> None:
    """Force the font size of every run in a cell's first paragraph,
    overriding whatever size it inherited from the template."""
    p = tc.find("w:p", NS)
    runs = p.findall("w:r", NS) if p is not None else []
    for run in runs:
        rpr = run.find("w:rPr", NS)
        if rpr is None:
            rpr = etree.Element(qn("w:rPr"))
            run.insert(0, rpr)
        for tag in ("w:sz", "w:szCs"):
            el = rpr.find(tag, NS)
            if el is None:
                el = etree.SubElement(rpr, qn(tag))
            el.set(qn("w:val"), str(size_half_points))


def set_cell_text_lines(tc: etree._Element, lines: list[str]) -> None:
    """Like set_cell_text, but splits `lines` across explicit line breaks
    within the cell's single paragraph instead of writing one run. Used
    where a row is allowed to grow (e.g. a wrapped to-do task) rather than
    staying a fixed single-line ruled slot."""
    p = tc.find("w:p", NS)
    if p is None:
        raise MagendaError("cell has no paragraph to write into")
    runs = p.findall("w:r", NS)
    base_rpr = runs[0].find("w:rPr", NS) if runs else p.find("w:pPr/w:rPr", NS)
    for r in runs:
        p.remove(r)
    for i, line in enumerate(lines):
        r = etree.SubElement(p, qn("w:r"))
        if base_rpr is not None:
            r.append(copy.deepcopy(base_rpr))
        if i > 0:
            etree.SubElement(r, qn("w:br"))
        t = etree.SubElement(r, qn("w:t"))
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = line


def set_run_text_at(p_or_tc: etree._Element, run_index: int, text: str) -> None:
    """Set the text of the Nth run within a paragraph/cell's first paragraph,
    leaving all other runs untouched. Used for cells like 'CW 21' or
    '19 TUESDAY' that are deliberately split across multiple runs."""
    p = p_or_tc if p_or_tc.tag == qn("w:p") else p_or_tc.find("w:p", NS)
    runs = p.findall("w:r", NS)
    if run_index >= len(runs):
        raise MagendaError(f"expected run index {run_index}, paragraph only has {len(runs)} runs")
    t = runs[run_index].find("w:t", NS)
    if t is None:
        t = etree.SubElement(runs[run_index], qn("w:t"))
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text


# --------------------------------------------------------------------------
# Calendar header/footer blocks
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CalendarBlock:
    """One title-row + weekday-row + day-number-row triplet. The weekday row
    (MON..SUN labels) never changes, so only title_row and dayno_row need edits."""
    title_row: etree._Element
    dayno_row: etree._Element


def _is_calendar_title_row(tr: etree._Element) -> bool:
    for r in _runs(tr):
        rpr = r.find("w:rPr", NS)
        if rpr is None:
            continue
        color = rpr.find("w:color", NS)
        font = rpr.find("w:rFonts", NS)
        sz = rpr.find("w:sz", NS)
        if (
            color is not None and color.get(qn("w:val")) == "0DB04B"
            and font is not None and font.get(qn("w:ascii")) == "Outfit Black"
            and sz is not None and sz.get(qn("w:val")) == "36"
        ):
            return True
    return False


def find_calendar_blocks(body: etree._Element) -> list[CalendarBlock]:
    """Find every calendar header/footer block in document order, regardless
    of which table it lives in (top-of-page headers and the embedded
    bottom-of-page footer share the exact same row signature)."""
    blocks: list[CalendarBlock] = []
    for tbl in body.findall(".//w:tbl", NS):
        rows = tbl.findall("w:tr", NS)
        for i, tr in enumerate(rows):
            if _is_calendar_title_row(tr) and i + 2 < len(rows):
                blocks.append(CalendarBlock(title_row=tr, dayno_row=rows[i + 2]))
    return blocks


def apply_calendar_block(block: CalendarBlock, fields: dict) -> None:
    """fields: output of calendar_math.header_fields()."""
    cells = block.title_row.findall("w:tc", NS)
    # cell 0: "<day>" + " <WEEKDAY>" (2 runs)
    set_run_text_at(cells[0], 0, fields["day"])
    set_run_text_at(cells[0], 1, " " + fields["weekday_name"])
    # cell 2: "CW " + "<n>" (2 runs) — index 2 because cell 1 is a blank spacer
    cw_runs = cells[2].find("w:p", NS).findall("w:r", NS)
    if len(cw_runs) >= 2:
        set_run_text_at(cells[2], 1, fields["cw"].split(" ", 1)[1])
    else:
        set_run_text_at(cells[2], 0, fields["cw"])
    # cell 3: month, cell 4: year (single run each)
    set_run_text_at(cells[3], 0, fields["month"])
    set_run_text_at(cells[4], 0, fields["year"])

    day_cells = block.dayno_row.findall("w:tc", NS)
    for i in range(7):
        set_cell_text(day_cells[i], str(fields["week_days"][i]))


# --------------------------------------------------------------------------
# "NEXT FOUR WEEKS" grid (page 1 only)
# --------------------------------------------------------------------------

def find_next_four_weeks_table(body: etree._Element) -> etree._Element:
    for tbl in body.findall("w:tbl", NS):
        rows = tbl.findall("w:tr", NS)
        if len(rows) == 5:
            header_cells = rows[0].findall("w:tc", NS)
            labels = [cell_text(c) for c in header_cells]
            if labels == ["", "M", "T", "W", "T", "F", "S", "S"]:
                return tbl
    raise MagendaError("could not locate the 'NEXT FOUR WEEKS' grid in this agenda")


def apply_next_four_weeks(table: etree._Element, weeks: list) -> None:
    rows = table.findall("w:tr", NS)[1:]  # skip header row
    if len(weeks) != len(rows):
        raise MagendaError(f"expected {len(rows)} weeks, got {len(weeks)}")
    for row, week in zip(rows, weeks):
        cells = row.findall("w:tc", NS)
        set_cell_text(cells[0], f"CW {week.iso_week}")
        for i, day in enumerate(week.days):
            set_cell_text(cells[i + 1], str(day.day))


# --------------------------------------------------------------------------
# To-do list (page 1 left column)
# --------------------------------------------------------------------------

TODO_ROW_CAPACITY = 18
TODO_TASK_MIN_FONT_SIZE = 18  # half-points (9pt) — floor before wrapping kicks in


def find_todo_table(body: etree._Element) -> etree._Element:
    for tbl in body.findall("w:tbl", NS):
        rows = tbl.findall("w:tr", NS)
        if len(rows) == TODO_ROW_CAPACITY + 1:
            header_cells = rows[0].findall("w:tc", NS)
            if len(header_cells) == 3 and cell_text(header_cells[1]) == "Task" and cell_text(header_cells[2]) == "Due":
                return tbl
    raise MagendaError("could not locate the to-do list table in this agenda")


def append_tasks(table: etree._Element, tasks: list[dict]) -> None:
    rows = table.findall("w:tr", NS)[1:]  # skip header row
    empty_rows = [r for r in rows if cell_text(r.findall("w:tc", NS)[1]) == "" and cell_text(r.findall("w:tc", NS)[2]) == ""]
    if len(tasks) > len(empty_rows):
        raise MagendaError(
            f"only {len(empty_rows)} free to-do row(s) left (capacity {TODO_ROW_CAPACITY}), "
            f"got {len(tasks)} task(s)"
        )
    for row, task in zip(empty_rows, tasks):
        cells = row.findall("w:tc", NS)
        family, default_size = cell_run_font(cells[1])
        lines, size = fit_downsize_or_wrap(
            task["text"],
            family=family,
            max_size_half_points=default_size,
            min_size_half_points=TODO_TASK_MIN_FONT_SIZE,
            max_width_twips=cell_text_width_twips(cells[1]),
        )
        set_cell_text_lines(cells[1], lines)
        set_run_size(cells[1], size)
        set_cell_text(cells[2], task.get("due", ""))


# --------------------------------------------------------------------------
# Daily schedule (page 1 right column)
# --------------------------------------------------------------------------

SCHEDULE_START_HOUR = 8   # 8am
SCHEDULE_END_HOUR = 18    # 6pm, inclusive
SCHEDULE_NOTES_FONT_SIZE = 24  # half-points (12pt) — matches the to-do list's default size

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _hour_label(hour24: int) -> str:
    if hour24 == 12:
        return "12pm"
    if hour24 > 12:
        return f"{hour24 - 12}pm"
    return f"{hour24}am"


def _parse_time(value: str) -> tuple[int, int]:
    m = _TIME_RE.match(value.strip())
    if not m:
        raise MagendaError(f"time must be in 24-hour HH:MM format, got {value!r}")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise MagendaError(f"time out of range: {value!r}")
    return hour, minute


def find_schedule_table(body: etree._Element) -> etree._Element:
    expected_rows = (SCHEDULE_END_HOUR - SCHEDULE_START_HOUR + 1) * 2
    for tbl in body.findall("w:tbl", NS):
        rows = tbl.findall("w:tr", NS)
        if len(rows) == expected_rows:
            first_cell_text = cell_text(rows[0].findall("w:tc", NS)[0])
            if first_cell_text == _hour_label(SCHEDULE_START_HOUR):
                return tbl
    raise MagendaError("could not locate the daily schedule table in this agenda")


def fill_schedule_entries(table: etree._Element, entries: list[dict]) -> None:
    """Each entry: {"time": "HH:MM", "text": ...}. The template has two rows
    per hour — one entry lands on exactly one row, chosen by minute (00-29 ->
    first row, 30-59 -> second row), never spanning multiple rows. Text that
    doesn't fit the row's width on one line is truncated (never wrapped)."""
    rows = table.findall("w:tr", NS)
    slot_rows: dict[str, list[etree._Element]] = {}
    current_hour = None
    for row in rows:
        first_cell = row.findall("w:tc", NS)[0]
        vmerge = first_cell.find("w:tcPr/w:vMerge", NS)
        if vmerge is not None and vmerge.get(qn("w:val")) == "restart":
            current_hour = cell_text(first_cell)
            slot_rows[current_hour] = [row]
        else:
            slot_rows[current_hour].append(row)

    seen = set()
    for entry in entries:
        hour, minute = _parse_time(entry["time"])
        if not (SCHEDULE_START_HOUR <= hour <= SCHEDULE_END_HOUR):
            raise MagendaError(
                f"time {entry['time']!r} is outside the schedule's range "
                f"({_hour_label(SCHEDULE_START_HOUR)}-{_hour_label(SCHEDULE_END_HOUR)})"
            )
        label = _hour_label(hour)
        half = 0 if minute < 30 else 1
        key = (label, half)
        if key in seen:
            raise MagendaError(f"two entries both land on the {entry['time']!r} slot in this call")
        seen.add(key)

        notes_cell = slot_rows[label][half].findall("w:tc", NS)[1]
        family, _ = cell_run_font(notes_cell)
        fitted = fit_single_line(
            entry["text"],
            family=family,
            size_half_points=SCHEDULE_NOTES_FONT_SIZE,
            max_width_twips=cell_text_width_twips(notes_cell),
        )
        set_cell_text(notes_cell, fitted)
        set_run_size(notes_cell, SCHEDULE_NOTES_FONT_SIZE)


# --------------------------------------------------------------------------
# Meeting pages
# --------------------------------------------------------------------------

MEETING_TITLE_PREFIX = "Meeting title: "
FURTHER_NOTES_TEXT = "Further notes from today"

# From the template's fixed sectPr/settings.xml — see assets/template.docx:
# pgSz.w=11900, pgMar.left=1134, pgMar.right=567 -> 11900-1134-567=10199.
PAGE_CONTENT_WIDTH_TWIPS = 10199
DEFAULT_TAB_STOP_TWIPS = 720


def _paragraph_text(p: etree._Element) -> str:
    return "".join(t.text or "" for t in p.findall(".//w:t", NS))


def find_meeting_unit_template(body: etree._Element) -> tuple[etree._Element, etree._Element, etree._Element]:
    """Return (calendar_header_table, title_paragraph, notes_table) for the
    first meeting page in the document — used both as the clone source and,
    when its title is still blank, as the first meeting slot to fill in place."""
    children = list(body)
    for i, el in enumerate(children):
        if el.tag == qn("w:p") and _paragraph_text(el).startswith(MEETING_TITLE_PREFIX):
            header_table = children[i - 1]
            notes_table = children[i + 1]
            if header_table.tag != qn("w:tbl") or notes_table.tag != qn("w:tbl"):
                raise MagendaError("meeting page template has an unexpected shape")
            return header_table, el, notes_table
    raise MagendaError("could not locate a meeting page template in this agenda")


def meeting_title_text(title_para: etree._Element) -> str:
    return _paragraph_text(title_para)[len(MEETING_TITLE_PREFIX):]


def _run_font(run: etree._Element) -> tuple[str, int]:
    rpr = run.find("w:rPr", NS)
    font_el = rpr.find("w:rFonts", NS) if rpr is not None else None
    sz_el = rpr.find("w:sz", NS) if rpr is not None else None
    family = font_el.get(qn("w:ascii")) if font_el is not None and font_el.get(qn("w:ascii")) else "Outfit"
    size = int(sz_el.get(qn("w:val"))) if sz_el is not None else 24
    return family, size


def _next_tab_stop(x_twips: float) -> float:
    return (int(x_twips // DEFAULT_TAB_STOP_TWIPS) + 1) * DEFAULT_TAB_STOP_TWIPS


def set_meeting_title(title_para: etree._Element, title: str) -> None:
    """Set the title text, truncated from the end (never wrapped) so it
    stays on one line. The title paragraph has no fixed-width cell to
    measure against — it starts after the 'Meeting title: ' label plus two
    default tab stops — so the available width has to be derived from the
    page's content width instead of a w:tcW."""
    runs = title_para.findall(".//w:r", NS)
    if len(runs) < 3:
        raise MagendaError("meeting title paragraph has an unexpected run structure")
    label_text = "".join(t.text or "" for t in runs[0].findall("w:t", NS))
    family, size = _run_font(runs[2])
    x = _next_tab_stop(text_width_twips(label_text, family=family, size_half_points=size))
    x = _next_tab_stop(x)
    fitted = fit_single_line(
        title,
        family=family,
        size_half_points=size,
        max_width_twips=PAGE_CONTENT_WIDTH_TWIPS - x,
    )
    set_run_text_at(title_para, 2, fitted)


def blank_meeting_title_slot(body: etree._Element) -> None:
    """Called once by create_agenda: the template ships with one pre-filled
    example meeting page. Clear its title so the first add_meeting call fills
    this existing slot in place instead of appending a duplicate page."""
    _, title_para, _ = find_meeting_unit_template(body)
    set_meeting_title(title_para, "")


def strip_meeting_notes_footer(body: etree._Element) -> None:
    """Called once by create_agenda: the template's meeting notes table
    ships with a trailing 3-row calendar block (title/weekday/day-number)
    baked onto its end. In the stock single-meeting template that's how the
    closing 'Further notes' page picked up a header for free, by natural
    page overflow. ensure_further_notes_page_break() now gives the closing
    page its own explicit header instead, which makes this trailing block
    dead weight — left in place it overflows onto its own near-empty page
    after every meeting, worse once meetings are cloned since every clone
    carries a copy.

    Drops one extra ruled-line row beyond that 3-row block (4 total):
    empirically, a meeting page reached via the hard page break that
    insert_meeting_page/ensure_further_notes_page_break insert needs very
    slightly more room than the same content does when it merely follows
    natural overflow (as the template's first, pre-filled meeting page
    does) — 23 ruled rows plus the header/title overflows by a hair and
    produces a fully blank extra page, 22 fits cleanly either way.

    Idempotent: a no-op if already stripped."""
    _, _, notes_table = find_meeting_unit_template(body)
    rows = notes_table.findall("w:tr", NS)
    if len(rows) >= 4 and _is_calendar_title_row(rows[-3]):
        for row in rows[-4:]:
            notes_table.remove(row)


def find_further_notes_paragraph(body: etree._Element) -> etree._Element:
    for el in body:
        if el.tag == qn("w:p") and _paragraph_text(el).strip() == FURTHER_NOTES_TEXT:
            return el
    raise MagendaError("could not locate the closing 'Further notes from today' page")


def _page_break_paragraph() -> etree._Element:
    """A paragraph containing only an explicit page break. Two purposes:
    (1) two <w:tbl> elements placed directly adjacent with no intervening
    paragraph get silently merged into one table by Word/LibreOffice, which
    is what was making a meeting's ruled lines/borders disappear and made
    the following meeting's header look duplicated; (2) it guarantees each
    new meeting (or the closing page) starts on a fresh page instead of
    sharing a page with whatever precedes it."""
    p = etree.Element(qn("w:p"))
    r = etree.SubElement(p, qn("w:r"))
    etree.SubElement(r, qn("w:br")).set(qn("w:type"), "page")
    return p


def _is_calendar_header_table(el: etree._Element) -> bool:
    return (
        el.tag == qn("w:tbl")
        and len(el.findall("w:tr", NS)) == 3
        and _is_calendar_title_row(el.findall("w:tr", NS)[0])
    )


def ensure_further_notes_page_break(body: etree._Element) -> None:
    """Called once by create_agenda: force the closing 'Further notes' page
    to always start on its own new page, regardless of how much the last
    meeting's own content happens to overflow. In the stock template, the
    closing page has no calendar header table of its own — it relies on
    borrowing whatever meeting happens to be last's trailing footer-calendar
    rows, which land on the same page purely by natural overflow. Forcing a
    hard page break breaks that accidental coupling, so give the closing
    page its own calendar header (cloned from the same source as every other
    page) to keep every page's look consistent, and put the page break
    directly before that header rather than before the 'Further notes'
    title, so the header and title stay together on the new page."""
    para = find_further_notes_paragraph(body)

    header = para.getprevious()
    if not _is_calendar_header_table(header):
        header_table, _, _ = find_meeting_unit_template(body)
        para.addprevious(copy.deepcopy(header_table))
        header = para.getprevious()

    before_header = header.getprevious()
    already_has_break = before_header is not None and any(
        b.get(qn("w:type")) == "page" for b in before_header.findall(".//w:br", NS)
    )
    if not already_has_break:
        header.addprevious(_page_break_paragraph())


def _new_meeting_insertion_anchor(body: etree._Element) -> etree._Element:
    """Where a newly cloned meeting page should be spliced in: right before
    the closing page's own break+header block if create_agenda already added
    one (so new meetings land before the closing page, not sandwiched
    between it and 'Further notes'), otherwise right before 'Further notes'
    itself."""
    para = find_further_notes_paragraph(body)
    header = para.getprevious()
    if not _is_calendar_header_table(header):
        return para
    before_header = header.getprevious()
    if before_header is not None and any(
        b.get(qn("w:type")) == "page" for b in before_header.findall(".//w:br", NS)
    ):
        return before_header
    return header


def insert_meeting_page(body: etree._Element, title: str) -> None:
    """Fill the first meeting page if its title slot is still blank
    (left that way by create_agenda); otherwise clone it and append a new
    meeting page before the closing 'Further notes' page."""
    header_table, title_para, notes_table = find_meeting_unit_template(body)
    if meeting_title_text(title_para) == "":
        set_meeting_title(title_para, title)
        return

    new_header = copy.deepcopy(header_table)
    new_title_para = copy.deepcopy(title_para)
    new_notes = copy.deepcopy(notes_table)
    set_meeting_title(new_title_para, title)

    anchor = _new_meeting_insertion_anchor(body)
    anchor.addprevious(_page_break_paragraph())
    anchor.addprevious(new_header)
    anchor.addprevious(new_title_para)
    anchor.addprevious(new_notes)
