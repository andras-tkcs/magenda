"""Low-level OOXML manipulation for the agenda template.

Every function here is a pure tree edit: locate a node by a stable structural
signature (font/color/text fingerprint baked into the template), then set
text or splice a cloned subtree. Formatting is never invented — it's either
copied from the template's own runs, or (where a section's row/page count is
data-driven, so no single template instance survives in the saved doc to
clone from — the delegated-tasks rows and pages, the page-break paragraph)
built from values transcribed byte-for-byte out of the template.

The calendar chrome (day/weekday/CW/month/year) lives in the document's Word
header part (word/header1.xml), not in the body — the template defines
exactly one instance there and Word/LibreOffice repeat it on every page via
each section's (inherited) headerReference, so it only ever needs editing
once (see find_calendar_block / agenda_store.AgendaDocument.header). The body
itself has no per-page calendar tables to clone, unlike the pre-header-based
template this replaced.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass

from lxml import etree

from magenda.text_fit import (
    fit_downsize_or_wrap,
    fit_single_line,
    text_line_height_twips,
    text_width_twips,
)

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
    """Set the Nth piece of text within a paragraph/cell's first paragraph,
    leaving everything else untouched. Used for cells like 'CW 21' or
    '19 TUESDAY' that are deliberately split into multiple pieces of text —
    addressed by <w:t> text node rather than by sibling <w:r>, since those
    pieces may live in separate runs or share one (LibreOffice merges runs
    with identical formatting on save)."""
    p = p_or_tc if p_or_tc.tag == qn("w:p") else p_or_tc.find("w:p", NS)
    texts = p.findall(".//w:t", NS)
    if run_index >= len(texts):
        raise MagendaError(f"expected text node {run_index}, paragraph only has {len(texts)}")
    t = texts[run_index]
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
            color is not None and color.get(qn("w:val")) == "215E99"
            and font is not None and font.get(qn("w:ascii")) == "Outfit Black"
            and sz is not None and sz.get(qn("w:val")) == "36"
        ):
            return True
    return False


def find_calendar_block(header: etree._Element) -> CalendarBlock:
    """The single calendar title/weekday/day-number block living in the
    document's Word header part (see agenda_store.AgendaDocument.header) —
    edited once here rather than once per page."""
    for tbl in header.findall(".//w:tbl", NS):
        rows = tbl.findall("w:tr", NS)
        for i, tr in enumerate(rows):
            if _is_calendar_title_row(tr) and i + 2 < len(rows):
                return CalendarBlock(title_row=tr, dayno_row=rows[i + 2])
    raise MagendaError("could not locate the calendar header block in the document header")


def apply_calendar_block(block: CalendarBlock, fields: dict) -> None:
    """fields: output of calendar_math.header_fields()."""
    cells = block.title_row.findall("w:tc", NS)
    # cell 0: "<day>" + " <WEEKDAY>", normally 2 text nodes, but LibreOffice
    # merges same-formatted text nodes into one on save — fall back to
    # writing the combined string into whichever single node remains.
    day_texts = cells[0].find("w:p", NS).findall(".//w:t", NS)
    if len(day_texts) >= 2:
        set_run_text_at(cells[0], 0, fields["day"])
        set_run_text_at(cells[0], 1, " " + fields["weekday_name"])
    else:
        set_run_text_at(cells[0], 0, fields["day"] + " " + fields["weekday_name"])
    # cell 2: "CW " + "<n>" — index 2 because cell 1 is a blank spacer.
    # Same merge fallback as cell 0.
    cw_texts = cells[2].find("w:p", NS).findall(".//w:t", NS)
    if len(cw_texts) >= 2:
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


def _set_vmerge(tc: etree._Element, restart: bool) -> None:
    """Mark a cell as starting (restart) or continuing a vertical merge.
    w:vMerge must sit right after w:tcW/w:gridSpan and before w:tcBorders in
    tcPr's content model, so it's inserted by position, not appended."""
    tcPr = tc.find("w:tcPr", NS)
    if tcPr is None:
        tcPr = etree.Element(qn("w:tcPr"))
        tc.insert(0, tcPr)
    vmerge = tcPr.find("w:vMerge", NS)
    if vmerge is None:
        vmerge = etree.Element(qn("w:vMerge"))
        tcW = tcPr.find("w:tcW", NS)
        tcPr.insert(list(tcPr).index(tcW) + 1 if tcW is not None else 0, vmerge)
    if restart:
        vmerge.set(qn("w:val"), "restart")
    elif qn("w:val") in vmerge.attrib:
        del vmerge.attrib[qn("w:val")]


def append_tasks(table: etree._Element, tasks: list[dict]) -> None:
    """Fill empty to-do rows top-down. A task whose text doesn't fit on one
    line even at TODO_TASK_MIN_FONT_SIZE spans multiple rows instead of
    growing a single row's height: growing one row's height throws off page
    1's tight layout (it's shared with the daily schedule, and there's no
    slack for a taller row) and does so regardless of which row grows, so
    rows are vertically merged (w:vMerge) instead — every to-do row keeps
    its normal single-line height, and a wrapped task consumes only as many
    of the 18 rows as its wrapped lines actually need at their (possibly
    downsized) font size, rather than one row per line: a row sized for one
    line at the default size can fit several lines once the font has
    shrunk, so charging one full row per line would leave large blank gaps
    below the text."""
    rows = table.findall("w:tr", NS)[1:]  # skip header row
    empty_rows = [r for r in rows if cell_text(r.findall("w:tc", NS)[1]) == "" and cell_text(r.findall("w:tc", NS)[2]) == ""]

    sample_row = empty_rows[0] if empty_rows else rows[0]
    sample_cells = sample_row.findall("w:tc", NS)
    family, default_size = cell_run_font(sample_cells[1])
    width = cell_text_width_twips(sample_cells[1])
    row_height = int(sample_row.find("w:trPr/w:trHeight", NS).get(qn("w:val")))

    plans = [
        fit_downsize_or_wrap(
            task["text"],
            family=family,
            max_size_half_points=default_size,
            min_size_half_points=TODO_TASK_MIN_FONT_SIZE,
            max_width_twips=width,
        )
        for task in tasks
    ]
    rows_per_plan = [
        max(1, math.ceil(len(lines) * text_line_height_twips(family, size) / row_height))
        for lines, size in plans
    ]
    rows_needed = sum(rows_per_plan)
    if rows_needed > len(empty_rows):
        raise MagendaError(
            f"only {len(empty_rows)} free to-do row(s) left (capacity {TODO_ROW_CAPACITY}), "
            f"need {rows_needed} row(s) for {len(tasks)} task(s)"
        )

    cursor = 0
    for task, (lines, size), row_count in zip(tasks, plans, rows_per_plan):
        group = empty_rows[cursor : cursor + row_count]
        cursor += row_count
        first_cells = group[0].findall("w:tc", NS)
        set_cell_text_lines(first_cells[1], lines)
        set_run_size(first_cells[1], size)
        set_cell_text(first_cells[2], task.get("due", ""))
        if len(group) > 1:
            for cell in first_cells:
                _set_vmerge(cell, restart=True)
            for row in group[1:]:
                for cell in row.findall("w:tc", NS):
                    set_cell_text(cell, "")
                    _set_vmerge(cell, restart=False)


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
# pgSz.w=11906, pgMar.left=1134, pgMar.right=567 -> 11906-1134-567=10205.
PAGE_CONTENT_WIDTH_TWIPS = 10205
DEFAULT_TAB_STOP_TWIPS = 720


def _paragraph_text(p: etree._Element) -> str:
    return "".join(t.text or "" for t in p.findall(".//w:t", NS))


def find_all_meeting_units(body: etree._Element) -> list[tuple[etree._Element, etree._Element]]:
    """Every meeting page's (title_paragraph, notes_table) pair, in document
    order — including a still-blank first slot. Each meeting occupies
    exactly one PDF page (see tests.test_meetings_render_one_page_each), so
    this list's order is also final PDF page order among the meeting pages."""
    units = []
    children = list(body)
    for i, el in enumerate(children):
        if el.tag == qn("w:p") and _paragraph_text(el).startswith(MEETING_TITLE_PREFIX):
            notes_table = children[i + 1]
            if notes_table.tag != qn("w:tbl"):
                raise MagendaError("meeting page template has an unexpected shape")
            units.append((el, notes_table))
    if not units:
        raise MagendaError("could not locate a meeting page template in this agenda")
    return units


def find_meeting_unit_template(body: etree._Element) -> tuple[etree._Element, etree._Element]:
    """Return (title_paragraph, notes_table) for the first meeting page in
    the document — used both as the clone source and, when its title is
    still blank, as the first meeting slot to fill in place. The calendar
    header isn't part of this unit — it lives in the Word header part (see
    find_calendar_block) and repeats on every page on its own, so there's
    nothing to clone for it here."""
    return find_all_meeting_units(body)[0]


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
    page's content width instead of a w:tcW.

    The label and title live in the trailing two <w:t> text nodes of the
    paragraph. They may be split across separate <w:r> runs or share a
    single run (LibreOffice merges runs with identical formatting on
    save) — either is valid OOXML, so this addresses text nodes directly
    rather than assuming a fixed number of sibling runs."""
    texts = title_para.findall(".//w:t", NS)
    if len(texts) < 2:
        raise MagendaError("meeting title paragraph has an unexpected run structure")
    label_text = texts[0].text or ""
    title_run = texts[-1].getparent()
    family, size = _run_font(title_run)
    x = _next_tab_stop(text_width_twips(label_text, family=family, size_half_points=size))
    x = _next_tab_stop(x)
    fitted = fit_single_line(
        title,
        family=family,
        size_half_points=size,
        max_width_twips=PAGE_CONTENT_WIDTH_TWIPS - x,
    )
    texts[-1].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    texts[-1].text = fitted


def blank_meeting_title_slot(body: etree._Element) -> None:
    """Called once by create_agenda: the template ships with one pre-filled
    example meeting page. Clear its title so the first add_meeting call fills
    this existing slot in place instead of appending a duplicate page."""
    title_para, _ = find_meeting_unit_template(body)
    set_meeting_title(title_para, "")


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


def _has_page_break(p: etree._Element) -> bool:
    return any(b.get(qn("w:type")) == "page" for b in p.findall(".//w:br", NS))


def ensure_further_notes_page_break(body: etree._Element) -> None:
    """Called once by create_agenda: guarantee the closing 'Further notes'
    page always starts on its own new page, regardless of how much the last
    meeting's own content happens to overflow. The template already ships
    with an explicit page break directly before 'Further notes' (there's no
    per-page calendar header table left in the body to keep in sync here —
    see the module docstring), so this is normally a no-op; it only adds one
    if a hand-built document is missing it. Idempotent."""
    para = find_further_notes_paragraph(body)
    before = para.getprevious()
    if before is None or before.tag != qn("w:p") or not _has_page_break(before):
        para.addprevious(_page_break_paragraph())


def insert_meeting_page(body: etree._Element, title: str) -> None:
    """Fill the first meeting page if its title slot is still blank
    (left that way by create_agenda); otherwise clone it and append a new
    meeting page before the closing 'Further notes' page."""
    title_para, notes_table = find_meeting_unit_template(body)
    if meeting_title_text(title_para) == "":
        set_meeting_title(title_para, title)
        return

    new_title_para = copy.deepcopy(title_para)
    new_notes = copy.deepcopy(notes_table)
    set_meeting_title(new_title_para, title)

    # Insert right before the page break that leads into 'Further notes' (so
    # new meetings land before the closing page, not after it) — falling
    # back to right before 'Further notes' itself if that break is somehow
    # missing.
    further_notes = find_further_notes_paragraph(body)
    before = further_notes.getprevious()
    anchor = before if before is not None and before.tag == qn("w:p") and _has_page_break(before) else further_notes

    anchor.addprevious(_page_break_paragraph())
    anchor.addprevious(new_title_para)
    anchor.addprevious(new_notes)


# --------------------------------------------------------------------------
# PDF navigation links (see magenda.pdf_links, which does the actual
# PDF-side linking — everything here is document-structure bookkeeping:
# which schedule text pairs with which meeting, and which final PDF page a
# meeting/the closing page lands on.)
# --------------------------------------------------------------------------

# The Word header's own "<< Overview" / "Notes >>" labels (see
# assets/template.docx, word/header1.xml, next to the week calendar) — used
# as PDF link-source text, since the header (and so these labels) repeats
# identically on every page.
OVERVIEW_LINK_LABEL = "<< Overview"
NOTES_LINK_LABEL = "Notes >>"


def read_daily_schedule_entries(body: etree._Element) -> list[str]:
    """Text of every currently-filled daily-schedule slot (page 1, right
    column), in row order."""
    table = find_schedule_table(body)
    texts = []
    for row in table.findall("w:tr", NS):
        notes_cell = row.findall("w:tc", NS)[1]
        text = cell_text(notes_cell)
        if text:
            texts.append(text)
    return texts


def match_schedule_to_meetings(body: etree._Element) -> list[tuple[str, int]]:
    """Pair each filled daily-schedule slot with the (0-indexed, document
    order) meeting it names, by prefix match: both a schedule entry's text
    and a meeting's title are independently truncated from the end (no
    ellipsis — see text_fit.fit_single_line) from whatever the caller
    typed, so for a schedule entry and a meeting that came from the same
    title, one is always a textual prefix of the other. A schedule entry
    with no matching meeting (it isn't one), or with more than one
    equally-plausible match, is left out rather than guessing."""
    titles = [meeting_title_text(title_para) for title_para, _ in find_all_meeting_units(body)]
    pairs = []
    for text in read_daily_schedule_entries(body):
        matches = [
            i for i, title in enumerate(titles)
            if title and (text.startswith(title) or title.startswith(text))
        ]
        if len(matches) == 1:
            pairs.append((text, matches[0]))
    return pairs


def meeting_page_index(body: etree._Element, meeting_index: int) -> int:
    """0-indexed final-PDF page number of the (0-indexed, document-order)
    meeting `meeting_index`, computed purely from document structure: page 0
    is the overview, followed by one page per delegated-tasks table
    currently in the doc (find_delegated_tables returns exactly one <w:tbl>
    per page — see rebuild_delegated_tasks), then one page per meeting in
    order."""
    return 1 + len(find_delegated_tables(body)) + meeting_index


# --------------------------------------------------------------------------
# Delegated tasks page(s)
# --------------------------------------------------------------------------

DELEGATED_HEADER_LABELS = ("", "Task & cadence", "Owner", "Status")
DELEGATED_MARK_FILL = "D6FCEC"
DELEGATED_HEADER_FILL = "D9D9D9"
DELEGATED_CADENCE_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
DELEGATED_CADENCE_ORDER = {"daily": 0, "weekly": 1, "monthly": 2}
DELEGATED_TASK_MAX_FONT_SIZE = 22  # half-points (11pt) — the template's own default, +1pt
DELEGATED_TASK_MIN_FONT_SIZE = 18  # half-points (9pt) — floor before wrapping kicks in, +1pt
DELEGATED_CADENCE_FONT_SIZE = 18  # half-points (9pt) — small label above the task text, +1pt
DELEGATED_BULLET_PREFIX = "• "  # bullet + a thin space, for a minimal bullet-to-text gap

# Determined empirically by rendering: rows no longer have a fixed height
# (the previous template's ~2cm blank free-space-for-notes paragraph is
# gone -- handwritten notes now live in the page's own footer, "Notes and
# updates", word/footer1.xml) so a row's actual height depends on how much
# its task/status text wraps. 8 is calibrated against a realistic worst
# case -- every column (task, owner, 2-line status) wrapping to its widest
# plausible content -- so it stays safe even when a page's tasks happen to
# be unusually verbose, at the cost of some unused room on a page of short
# one-line tasks.
DELEGATED_ROWS_PER_PAGE = 8

_THICK_BORDER_SZ = 24
_THIN_BORDER_SZ = 4

_DELEGATED_COLUMN_WIDTHS = {"number": 615, "task": 3464, "owner": 1689, "status": 4580}
_DELEGATED_COLUMN_ORDER = ("number", "task", "owner", "status")


def _is_delegated_tasks_table(tbl: etree._Element) -> bool:
    rows = tbl.findall("w:tr", NS)
    if not rows:
        return False
    cells = rows[0].findall("w:tc", NS)
    if len(cells) != 4:
        return False
    return tuple(cell_text(c) for c in cells) == DELEGATED_HEADER_LABELS


def find_delegated_tables(body: etree._Element) -> list[etree._Element]:
    """Every delegated-tasks table in the document, in document order — one
    per page the task list currently spans."""
    return [tbl for tbl in body.findall("w:tbl", NS) if _is_delegated_tasks_table(tbl)]


def _delegated_page_unit(tasks_table: etree._Element) -> tuple[etree._Element, etree._Element]:
    """(spacer_paragraph, tasks_table) for a delegated-tasks table. There's
    no per-page calendar header to account for here (see the module
    docstring) -- just the blank paragraph the template leaves above the
    table for a little breathing room under the page's Word header."""
    spacer = tasks_table.getprevious()
    if spacer is None or spacer.tag != qn("w:p"):
        raise MagendaError("delegated tasks page has an unexpected shape")
    return spacer, tasks_table


def _paragraph_is_blank(p: etree._Element) -> bool:
    return not "".join(t.text or "" for t in p.findall(".//w:t", NS)).strip()


def _find_page1_section_end(body: etree._Element) -> etree._Element:
    """The paragraph ending page 1's own two-column section (headerReference
    only, no footerReference, section type "continuous") -- always present
    in the template's fixed skeleton, totally independent of whether any
    delegated tasks currently exist. Whatever immediately follows it is
    either the delegated-tasks section (spacer + table(s) + that section's
    own footerReference-carrying boundary paragraph) if one currently
    exists, or straight into the meetings section if not -- the fixed point
    the whole delegated-tasks section is spliced in after / removed back
    to."""
    for p in body.findall("w:p", NS):
        sectPr = p.find("w:pPr/w:sectPr", NS)
        if sectPr is not None and sectPr.find("w:headerReference", NS) is not None:
            return p
    raise MagendaError("could not locate the end of page 1's own section")


def _find_delegated_section_boundary(body: etree._Element) -> etree._Element:
    """The paragraph carrying the sectPr that closes the delegated-tasks
    section -- the point up to which word/footer1.xml's "Notes and updates"
    footer applies. Only present while at least one delegated-tasks table
    exists (see remove_delegated_tasks_page / _insert_delegated_tasks_page)
    -- a lone empty section between page 1 and the meetings section would
    otherwise still consume a blank page of its own (an OOXML section, even
    with no real content, still forces the page break its own type implies),
    so the whole section is added and removed as one unit rather than left
    in place empty. Identified as the only paragraph-embedded sectPr with a
    footerReference -- the other paragraph-embedded sectPr (page 1's own,
    see _find_page1_section_end) only carries a headerReference, and the
    document's final section (the meetings/closing page's own footer2
    reference) is the body's own trailing sectPr, not a paragraph's."""
    for p in body.findall("w:p", NS):
        sectPr = p.find("w:pPr/w:sectPr", NS)
        if sectPr is not None and sectPr.find("w:footerReference", NS) is not None:
            return p
    raise MagendaError("could not locate the delegated-tasks section boundary")


# Transcribed byte-for-byte from the template's own delegated-tasks section
# boundary (see assets/template.docx, word/document.xml) -- the exact
# pgSz/pgMar/cols the template uses for this section, and the relationship
# id word/_rels/document.xml.rels binds to footer1.xml ("Notes and
# updates"). Needed here (rather than only ever cloned from the template)
# because the whole section is removed when no delegated tasks exist (see
# remove_delegated_tasks_page) -- there is then no instance left in the
# document to clone from once add_delegated_tasks needs it again.
_DELEGATED_SECTION_FOOTER_RID = "rId8"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _delegated_section_boundary_paragraph() -> etree._Element:
    p = etree.Element(qn("w:p"))
    pPr = etree.SubElement(p, qn("w:pPr"))
    sectPr = etree.SubElement(pPr, qn("w:sectPr"))
    footerRef = etree.SubElement(sectPr, qn("w:footerReference"))
    footerRef.set(qn("w:type"), "default")
    footerRef.set(f"{{{_R_NS}}}id", _DELEGATED_SECTION_FOOTER_RID)
    pgSz = etree.SubElement(sectPr, qn("w:pgSz"))
    pgSz.set(qn("w:w"), "11906")
    pgSz.set(qn("w:h"), "16838")
    pgMar = etree.SubElement(sectPr, qn("w:pgMar"))
    for attr, val in (
        ("top", "567"), ("right", "567"), ("bottom", "567"), ("left", "1134"),
        ("header", "0"), ("footer", "0"), ("gutter", "0"),
    ):
        pgMar.set(qn(f"w:{attr}"), val)
    cols = etree.SubElement(sectPr, qn("w:cols"))
    cols.set(qn("w:space"), "720")
    return p


def remove_delegated_tasks_page(body: etree._Element) -> None:
    """Called once by create_agenda: the template ships with one delegated
    tasks page, pre-populated with 4 example rows (2 marked, 2 unmarked)
    purely to illustrate the marked/unmarked look. A fresh agenda has no
    delegated tasks yet, so drop the page entirely rather than shipping a
    near-empty page — add_delegated_tasks re-creates it (see
    _insert_delegated_tasks_page) the first time it's actually needed, and
    rebuild_delegated_tasks removes it again if it's ever emptied back out.

    Removes the whole delegated-tasks section as one unit: the leading
    spacer paragraph, every table (plus any page breaks/spacers between
    them if the list had spanned multiple pages), and the section's own
    boundary paragraph (see _find_delegated_section_boundary) -- unlike a
    plain page break, a section boundary left in place with nothing before
    it still consumes a blank page of its own, so it has to go too rather
    than stay behind as an always-present anchor."""
    tables = find_delegated_tables(body)
    if not tables:
        return
    boundary = _find_delegated_section_boundary(body)
    spacer, _first_table = _delegated_page_unit(tables[0])
    node = spacer
    while node is not None:
        nxt = node.getnext()
        is_boundary = node is boundary
        body.remove(node)
        if is_boundary:
            break
        node = nxt


def _delegated_header_rpr() -> etree._Element:
    rpr = etree.Element(qn("w:rPr"))
    fonts = etree.SubElement(rpr, qn("w:rFonts"))
    fonts.set(qn("w:ascii"), "Outfit Black")
    fonts.set(qn("w:hAnsi"), "Outfit Black")
    etree.SubElement(rpr, qn("w:b"))
    etree.SubElement(rpr, qn("w:bCs"))
    etree.SubElement(rpr, qn("w:caps"))
    color = etree.SubElement(rpr, qn("w:color"))
    color.set(qn("w:val"), "BF4E14")
    sz = etree.SubElement(rpr, qn("w:sz"))
    sz.set(qn("w:val"), "28")
    szCs = etree.SubElement(rpr, qn("w:szCs"))
    szCs.set(qn("w:val"), "32")
    return rpr


def _build_delegated_table_shell() -> etree._Element:
    """A delegated-tasks table with just its header row (a blank row-number
    column, then Task & cadence / Owner / Status), transcribed byte-for-byte
    from the template's own table. Used to (re-)create the delegated tasks
    page from scratch when it doesn't currently exist in the document — see
    _insert_delegated_tasks_page — since a variable, per-date number of data
    rows means there's no single template table left in the saved doc once
    the page has been removed (remove_delegated_tasks_page) or spans more
    than one page.

    The header row carries w:trPr/w:tblHeader, so Word/LibreOffice repeat it
    automatically if this table's own rows ever make it break across pages
    on their own (DELEGATED_ROWS_PER_PAGE is a worst-case estimate, not a
    guarantee -- unusually tall wrapped content can still overflow one
    page). Explicit page-driven breaks (rebuild_delegated_tasks starting a
    new page once DELEGATED_ROWS_PER_PAGE is reached) already get a fresh
    header via a brand new call to this function, so this only matters for
    that unplanned-overflow case."""
    tbl = etree.Element(qn("w:tbl"))
    tblPr = etree.SubElement(tbl, qn("w:tblPr"))
    style = etree.SubElement(tblPr, qn("w:tblStyle"))
    style.set(qn("w:val"), "TableGrid")
    tblW = etree.SubElement(tblPr, qn("w:tblW"))
    tblW.set(qn("w:w"), str(sum(_DELEGATED_COLUMN_WIDTHS.values())))
    tblW.set(qn("w:type"), "dxa")
    borders = etree.SubElement(tblPr, qn("w:tblBorders"))
    for tag in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = etree.SubElement(borders, qn(f"w:{tag}"))
        b.set(qn("w:val"), "none")
        b.set(qn("w:sz"), "0")
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "auto")
    tblGrid = etree.SubElement(tbl, qn("w:tblGrid"))
    for key in _DELEGATED_COLUMN_ORDER:
        col = etree.SubElement(tblGrid, qn("w:gridCol"))
        col.set(qn("w:w"), str(_DELEGATED_COLUMN_WIDTHS[key]))

    header_row = etree.SubElement(tbl, qn("w:tr"))
    trPr = etree.SubElement(header_row, qn("w:trPr"))
    etree.SubElement(trPr, qn("w:tblHeader"))
    labels = {"number": "", "task": "Task & cadence", "owner": "Owner", "status": "Status"}
    for key in _DELEGATED_COLUMN_ORDER:
        tc = etree.SubElement(header_row, qn("w:tc"))
        tcPr = etree.SubElement(tc, qn("w:tcPr"))
        tcW = etree.SubElement(tcPr, qn("w:tcW"))
        tcW.set(qn("w:w"), str(_DELEGATED_COLUMN_WIDTHS[key]))
        tcW.set(qn("w:type"), "dxa")
        _tc_borders(tcPr, _THICK_BORDER_SZ, _THICK_BORDER_SZ)
        shd = etree.SubElement(tcPr, qn("w:shd"))
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), DELEGATED_HEADER_FILL)
        p = etree.SubElement(tc, qn("w:p"))
        pPr = etree.SubElement(p, qn("w:pPr"))
        spacing = etree.SubElement(pPr, qn("w:spacing"))
        spacing.set(qn("w:after"), "0")
        spacing.set(qn("w:line"), "240")
        spacing.set(qn("w:lineRule"), "auto")
        jc = etree.SubElement(pPr, qn("w:jc"))
        jc.set(qn("w:val"), "center")
        label = labels[key]
        if label:
            r = etree.SubElement(p, qn("w:r"))
            r.append(_delegated_header_rpr())
            t = etree.SubElement(r, qn("w:t"))
            t.text = label
    return tbl


def _insert_delegated_tasks_page(body: etree._Element) -> tuple[etree._Element, etree._Element]:
    """Create the whole delegated-tasks section from scratch -- spacer
    paragraph, table-with-header-row-only, and the section's own boundary
    paragraph (see _delegated_section_boundary_paragraph) -- and splice it
    in right after page 1's own section ends (_find_page1_section_end),
    returning the (spacer, table) pair. Only called when no delegated-tasks
    table currently exists, i.e. the boundary paragraph doesn't exist either
    (see remove_delegated_tasks_page) -- both are created together here so
    the section is never left half-present."""
    anchor = _find_page1_section_end(body).getnext()
    spacer = etree.Element(qn("w:p"))
    table = _build_delegated_table_shell()
    boundary = _delegated_section_boundary_paragraph()
    anchor.addprevious(spacer)
    anchor.addprevious(table)
    anchor.addprevious(boundary)
    return spacer, table


def _delegated_body_rpr() -> etree._Element:
    """Run properties for delegated-row body text (Outfit ExtraLight, 11pt)
    — rows are built programmatically since their count varies per date, so
    there's no single template row left in the saved doc to clone from once
    remove_delegated_tasks_page has run. Colored plain black rather than the
    label_color accent used by the row numbers and column headers (TO-DO
    LIST, Task & cadence/Owner/Status) -- task/owner/status text stays black
    regardless of theme, unlike those two -- and rather than the template's
    own sample rows' color (F95738, a leftover from the previous template's
    palette) -- both deliberate overrides, not transcriptions."""
    rpr = etree.Element(qn("w:rPr"))
    fonts = etree.SubElement(rpr, qn("w:rFonts"))
    fonts.set(qn("w:ascii"), "Outfit ExtraLight")
    fonts.set(qn("w:hAnsi"), "Outfit ExtraLight")
    color = etree.SubElement(rpr, qn("w:color"))
    color.set(qn("w:val"), "000000")
    for tag in ("w:sz", "w:szCs"):
        sz = etree.SubElement(rpr, qn(tag))
        sz.set(qn("w:val"), "22")
    return rpr


def _tc_borders(tcPr: etree._Element, top_sz: int, bottom_sz: int | None) -> None:
    borders = etree.SubElement(tcPr, qn("w:tcBorders"))
    top = etree.SubElement(borders, qn("w:top"))
    top.set(qn("w:val"), "single")
    top.set(qn("w:sz"), str(top_sz))
    top.set(qn("w:space"), "0")
    top.set(qn("w:color"), "auto")
    if bottom_sz is not None:
        bottom = etree.SubElement(borders, qn("w:bottom"))
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), str(bottom_sz))
        bottom.set(qn("w:space"), "0")
        bottom.set(qn("w:color"), "auto")


def _delegated_cell(
    width: int, top_sz: int, bottom_sz: int | None, marked: bool, center: bool = False
) -> etree._Element:
    tc = etree.Element(qn("w:tc"))
    tcPr = etree.SubElement(tc, qn("w:tcPr"))
    tcW = etree.SubElement(tcPr, qn("w:tcW"))
    tcW.set(qn("w:w"), str(width))
    tcW.set(qn("w:type"), "dxa")
    _tc_borders(tcPr, top_sz, bottom_sz)
    if marked:
        shd = etree.SubElement(tcPr, qn("w:shd"))
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), DELEGATED_MARK_FILL)
    p = etree.SubElement(tc, qn("w:p"))
    pPr = etree.SubElement(p, qn("w:pPr"))
    spacing = etree.SubElement(pPr, qn("w:spacing"))
    spacing.set(qn("w:before"), "240")
    if center:
        jc = etree.SubElement(pPr, qn("w:jc"))
        jc.set(qn("w:val"), "center")
    pPr.append(_delegated_body_rpr())
    return tc


def _delegated_row(marked: bool, top_sz: int, bottom_sz: int | None, number: int) -> etree._Element:
    tr = etree.Element(qn("w:tr"))
    for key in _DELEGATED_COLUMN_ORDER:
        tr.append(
            _delegated_cell(
                _DELEGATED_COLUMN_WIDTHS[key], top_sz, bottom_sz, marked, center=(key == "owner")
            )
        )
    number_cell = tr.findall("w:tc", NS)[0]
    number_p = number_cell.find("w:p", NS)
    # Overwrite the paragraph-mark rPr that _delegated_cell put here (the
    # black body-text one, appropriate for the other 3 columns) with the
    # header/label one, so the number column stays label_color-themed
    # end to end -- both the actual run below and the mark formatting an
    # empty paragraph would otherwise fall back to.
    old_mark_rpr = number_p.find("w:pPr/w:rPr", NS)
    if old_mark_rpr is not None:
        old_mark_rpr.getparent().remove(old_mark_rpr)
    number_p.find("w:pPr", NS).append(_delegated_header_rpr())
    r = etree.SubElement(number_p, qn("w:r"))
    r.append(_delegated_header_rpr())
    t = etree.SubElement(r, qn("w:t"))
    t.text = str(number)
    return tr


def _strip_row_bottom_border(tr: etree._Element) -> None:
    """The template's own last sample row leaves its bottom edge open (no
    ruled line closing off the table) — applied to the final row of the
    final page once the full row count is known."""
    for tc in tr.findall("w:tc", NS):
        borders = tc.find("w:tcPr/w:tcBorders", NS)
        if borders is not None:
            bottom = borders.find("w:bottom", NS)
            if bottom is not None:
                borders.remove(bottom)


def _set_task_cadence_cell(tc: etree._Element, text: str, cadence: str) -> None:
    width = cell_text_width_twips(tc)
    family, _ = cell_run_font(tc)
    lines, size = fit_downsize_or_wrap(
        text,
        family=family,
        max_size_half_points=DELEGATED_TASK_MAX_FONT_SIZE,
        min_size_half_points=DELEGATED_TASK_MIN_FONT_SIZE,
        max_width_twips=width,
    )
    all_lines = [DELEGATED_CADENCE_LABELS[cadence]] + lines
    set_cell_text_lines(tc, all_lines)
    runs = tc.find("w:p", NS).findall("w:r", NS)
    for tag in ("w:sz", "w:szCs"):
        el = runs[0].find(f"w:rPr/{tag}", NS)
        if el is not None:
            el.set(qn("w:val"), str(DELEGATED_CADENCE_FONT_SIZE))
    for run in runs[1:]:
        for tag in ("w:sz", "w:szCs"):
            el = run.find(f"w:rPr/{tag}", NS)
            if el is not None:
                el.set(qn("w:val"), str(size))


def _set_owner_cell(tc: etree._Element, owner: str) -> None:
    if not owner:
        return
    family, size = cell_run_font(tc)
    fitted = fit_single_line(
        owner, family=family, size_half_points=size, max_width_twips=cell_text_width_twips(tc)
    )
    set_cell_text(tc, fitted)


def _set_status_cell(tc: etree._Element, status: str) -> None:
    lines = [line.strip() for line in status.split("\n") if line.strip()] if status else []
    if not lines:
        return
    family, size = cell_run_font(tc)
    bullet_width = text_width_twips(DELEGATED_BULLET_PREFIX, family=family, size_half_points=size)
    width = cell_text_width_twips(tc) - bullet_width
    fitted = [
        fit_single_line(line, family=family, size_half_points=size, max_width_twips=width)
        for line in lines
    ]
    set_cell_text_lines(tc, [f"{DELEGATED_BULLET_PREFIX}{line}" for line in fitted])


def _fill_delegated_row(tr: etree._Element, task: dict) -> None:
    cells = tr.findall("w:tc", NS)
    _set_task_cadence_cell(cells[1], task["text"], task.get("cadence", "daily"))
    _set_owner_cell(cells[2], task.get("owner", ""))
    _set_status_cell(cells[3], task.get("status", ""))


def _row_marked(tr: etree._Element) -> bool:
    tc = tr.findall("w:tc", NS)[0]
    shd = tc.find("w:tcPr/w:shd", NS)
    return shd is not None and (shd.get(qn("w:fill")) or "").upper() == DELEGATED_MARK_FILL


def _paragraph_lines(p: etree._Element) -> list[str]:
    """Text content of a paragraph built by set_cell_text_lines: one or more
    runs joined by <w:br/> line breaks, split back into one string per
    line."""
    lines: list[str] = []
    current: list[str] = []
    for node in p:
        if node.tag != qn("w:r"):
            continue
        for child in node:
            if child.tag == qn("w:t"):
                current.append(child.text or "")
            elif child.tag == qn("w:br"):
                lines.append("".join(current))
                current = []
    lines.append("".join(current))
    return lines


def read_delegated_tasks(body: etree._Element) -> list[dict]:
    """Every delegated task currently on the page(s), in document order,
    parsed back out of the table XML (there is no separate data model — the
    docx is the only state, per agenda_store's module docstring). The
    row-number column (cells[0]) is purely positional/derived — it's never
    read back, only recomputed by rebuild_delegated_tasks."""
    tasks: list[dict] = []
    for table in find_delegated_tables(body):
        for tr in table.findall("w:tr", NS)[1:]:
            cells = tr.findall("w:tc", NS)
            lines = _paragraph_lines(cells[1].find("w:p", NS))
            if not any(lines):
                continue  # a leftover blank row shouldn't normally exist, but skip rather than crash
            cadence_label = lines[0].strip().lower()
            cadence = next(
                (k for k, v in DELEGATED_CADENCE_LABELS.items() if v.lower() == cadence_label),
                "daily",
            )
            text = " ".join(lines[1:]).strip()

            status_lines = [
                line[len(DELEGATED_BULLET_PREFIX):] if line.startswith(DELEGATED_BULLET_PREFIX) else line
                for line in _paragraph_lines(cells[3].find("w:p", NS))
                if line.strip()
            ]

            tasks.append(
                {
                    "text": text,
                    "owner": cell_text(cells[2]),
                    "cadence": cadence,
                    "status": "\n".join(status_lines),
                    "marked": _row_marked(tr),
                }
            )
    return tasks


def rebuild_delegated_tasks(body: etree._Element, tasks: list[dict]) -> None:
    """Replace every delegated-tasks row across every page with `tasks` (the
    full, already-ordered list — see tools.add_delegated_tasks, which reads
    the existing rows back, merges in the new ones, and re-sorts before
    calling this). Extra pages beyond what's needed are dropped, and as many
    new ones as needed are cloned, so there is never a trailing empty row or
    an under-full trailing page. If `tasks` is empty, the whole page is
    removed (or left absent) rather than shown with just a header row — see
    the "skip the page entirely when nothing is delegated" requirement."""
    tables = find_delegated_tables(body)

    if not tasks:
        remove_delegated_tasks_page(body)
        return

    if tables:
        first_spacer, first_table = _delegated_page_unit(tables[0])
        for extra_table in tables[1:]:
            spacer, table = _delegated_page_unit(extra_table)
            page_break = spacer.getprevious()
            body.remove(table)
            body.remove(spacer)
            if page_break is not None and page_break.tag == qn("w:p") and _has_page_break(page_break):
                body.remove(page_break)
        for row in first_table.findall("w:tr", NS)[1:]:
            first_table.remove(row)
    else:
        first_spacer, first_table = _insert_delegated_tasks_page(body)

    # Only findable now: _insert_delegated_tasks_page creates the boundary
    # paragraph together with the rest of the section when neither existed.
    boundary = _find_delegated_section_boundary(body)

    current_table = first_table
    row_on_page = 0
    row_number = 0
    last_row: etree._Element | None = None
    for task in tasks:
        if row_on_page >= DELEGATED_ROWS_PER_PAGE:
            new_spacer = copy.deepcopy(first_spacer)
            new_table = _build_delegated_table_shell()  # header-row-only shell to fill
            boundary.addprevious(_page_break_paragraph())
            boundary.addprevious(new_spacer)
            boundary.addprevious(new_table)
            current_table = new_table
            row_on_page = 0

        row_number += 1
        top_sz = _THICK_BORDER_SZ if row_on_page == 0 else _THIN_BORDER_SZ
        tr = _delegated_row(bool(task.get("marked")), top_sz, _THIN_BORDER_SZ, row_number)
        _fill_delegated_row(tr, task)
        current_table.append(tr)
        last_row = tr
        row_on_page += 1

    if last_row is not None:
        _strip_row_bottom_border(last_row)
