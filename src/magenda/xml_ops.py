"""Low-level OOXML manipulation for the agenda template.

Every function here is a pure tree edit: locate a node by a stable structural
signature (font/color/text fingerprint baked into the template), then set
text or splice a cloned subtree. Formatting is never invented — it's either
copied from the template's own runs, or (where a section's row/page count is
data-driven, so no single template instance survives in the saved doc to
clone from — the delegated-tasks rows and pages, the page-break paragraph)
built from values transcribed byte-for-byte out of the template.
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
    title, so the header and title stay together on the new page.

    The closing page's own ruled-notes table ships with 23 rows in the
    template — fine when the page is reached by natural overflow (as in the
    stock template), but the same empirically-observed overflow described in
    strip_meeting_notes_footer applies once it's reached via a hard page
    break instead: 23 rows plus the header/title is a hair too tall and
    produces a fully blank trailing page; 22 fits cleanly. Trim one row to
    match."""
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

    closing_table = para.getnext()
    if closing_table is not None and closing_table.tag == qn("w:tbl"):
        rows = closing_table.findall("w:tr", NS)
        if len(rows) > 22:
            closing_table.remove(rows[-1])


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


# --------------------------------------------------------------------------
# Delegated tasks page(s)
# --------------------------------------------------------------------------

DELEGATED_HEADER_LABELS = ("Task & cadence", "Owner", "Status & notes")
DELEGATED_MARK_FILL = "D6FCEC"
DELEGATED_CADENCE_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
DELEGATED_CADENCE_ORDER = {"daily": 0, "weekly": 1, "monthly": 2}
DELEGATED_TASK_MAX_FONT_SIZE = 20  # half-points (10pt) — the template's own default
DELEGATED_TASK_MIN_FONT_SIZE = 16  # half-points (8pt) — floor before wrapping kicks in
DELEGATED_CADENCE_FONT_SIZE = 16  # half-points (8pt) — small label above the task text
DELEGATED_BULLET_PREFIX = "• "  # bullet + a thin space, for a minimal bullet-to-text gap
DELEGATED_NOTES_FREE_SPACE_TWIPS = 1000  # ~2cm blank room below status for handwritten updates

# Determined empirically by rendering (see render.py / the test suite) the
# same way the meeting-notes row counts were: how many data rows of this
# exact shape (font, spacing, free-space-for-notes) fit under the page's
# calendar header + column header before LibreOffice pushes the next row to
# a fresh page. Every row is ~93pt tall regardless of task-text length: that
# height is set by the status cell's free-space paragraph, which dwarfs a
# wrapped 2-3 line task or a downsized owner name, so this holds even when
# those wrap.
DELEGATED_ROWS_PER_PAGE = 7

_THICK_BORDER_SZ = 24
_THIN_BORDER_SZ = 4

_DELEGATED_COLUMN_WIDTHS = {"task": 3155, "owner": 1334, "status": 5859}


def _is_delegated_tasks_table(tbl: etree._Element) -> bool:
    rows = tbl.findall("w:tr", NS)
    if not rows:
        return False
    cells = rows[0].findall("w:tc", NS)
    if len(cells) != 3:
        return False
    return tuple(cell_text(c) for c in cells) == DELEGATED_HEADER_LABELS


def find_delegated_tables(body: etree._Element) -> list[etree._Element]:
    """Every delegated-tasks table in the document, in document order — one
    per page the task list currently spans."""
    return [tbl for tbl in body.findall("w:tbl", NS) if _is_delegated_tasks_table(tbl)]


def _delegated_page_unit(
    tasks_table: etree._Element,
) -> tuple[etree._Element, etree._Element, etree._Element]:
    """(calendar_header_table, spacer_paragraph, tasks_table) for the page a
    delegated-tasks table lives on."""
    spacer = tasks_table.getprevious()
    header = spacer.getprevious() if spacer is not None else None
    if spacer is None or spacer.tag != qn("w:p") or header is None or not _is_calendar_header_table(header):
        raise MagendaError("delegated tasks page has an unexpected shape")
    return header, spacer, tasks_table


def _paragraph_is_blank(p: etree._Element) -> bool:
    return not "".join(t.text or "" for t in p.findall(".//w:t", NS)).strip()


def remove_delegated_tasks_page(body: etree._Element) -> None:
    """Called once by create_agenda: the template ships with one delegated
    tasks page, pre-populated with 4 example rows (2 marked, 2 unmarked)
    purely to illustrate the marked/unmarked look. A fresh agenda has no
    delegated tasks yet, so drop the page entirely rather than shipping a
    near-empty page — add_delegated_tasks re-creates it (see
    _insert_delegated_tasks_page) the first time it's actually needed, and
    rebuild_delegated_tasks removes it again if it's ever emptied back out.

    Also drops the forced page break (plus any blank spacer paragraph)
    immediately following the table: that break only exists to end the
    delegated page's own — much-shorter-than-a-page — content and force a
    fresh page for whatever comes next (normally meeting page 1). Leaving it
    behind once the delegated page is gone forces an extra blank page,
    because page 1's own content already ends exactly at a page boundary on
    its own (natural overflow, same as the pre-delegated-page template)."""
    tables = find_delegated_tables(body)
    if not tables:
        return
    header, spacer, table = _delegated_page_unit(tables[0])
    trailing = table.getnext()
    body.remove(table)
    body.remove(spacer)
    body.remove(header)
    while trailing is not None and trailing.tag == qn("w:p") and _paragraph_is_blank(trailing):
        nxt = trailing.getnext()
        body.remove(trailing)
        trailing = nxt


def _delegated_header_rpr() -> etree._Element:
    rpr = etree.Element(qn("w:rPr"))
    fonts = etree.SubElement(rpr, qn("w:rFonts"))
    fonts.set(qn("w:ascii"), "Outfit Black")
    fonts.set(qn("w:hAnsi"), "Outfit Black")
    etree.SubElement(rpr, qn("w:b"))
    etree.SubElement(rpr, qn("w:bCs"))
    etree.SubElement(rpr, qn("w:caps"))
    color = etree.SubElement(rpr, qn("w:color"))
    color.set(qn("w:val"), "F95738")
    sz = etree.SubElement(rpr, qn("w:sz"))
    sz.set(qn("w:val"), "28")
    szCs = etree.SubElement(rpr, qn("w:szCs"))
    szCs.set(qn("w:val"), "32")
    return rpr


def _build_delegated_table_shell() -> etree._Element:
    """A delegated-tasks table with just its header row (Task & cadence /
    Owner / Status & notes), transcribed byte-for-byte from the template's
    own table. Used to (re-)create the delegated tasks page from scratch
    when it doesn't currently exist in the document — see
    _insert_delegated_tasks_page — since a variable, per-date number of data
    rows means there's no single template table left in the saved doc once
    the page has been removed (remove_delegated_tasks_page) or spans more
    than one page."""
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
    for key in ("task", "owner", "status"):
        col = etree.SubElement(tblGrid, qn("w:gridCol"))
        col.set(qn("w:w"), str(_DELEGATED_COLUMN_WIDTHS[key]))

    header_row = etree.SubElement(tbl, qn("w:tr"))
    for key, label in (("task", "Task & cadence"), ("owner", "Owner"), ("status", "Status & notes")):
        tc = etree.SubElement(header_row, qn("w:tc"))
        tcPr = etree.SubElement(tc, qn("w:tcPr"))
        tcW = etree.SubElement(tcPr, qn("w:tcW"))
        tcW.set(qn("w:w"), str(_DELEGATED_COLUMN_WIDTHS[key]))
        tcW.set(qn("w:type"), "dxa")
        _tc_borders(tcPr, _THICK_BORDER_SZ, _THICK_BORDER_SZ)
        p = etree.SubElement(tc, qn("w:p"))
        pPr = etree.SubElement(p, qn("w:pPr"))
        spacing = etree.SubElement(pPr, qn("w:spacing"))
        spacing.set(qn("w:after"), "0")
        spacing.set(qn("w:line"), "240")
        spacing.set(qn("w:lineRule"), "auto")
        jc = etree.SubElement(pPr, qn("w:jc"))
        jc.set(qn("w:val"), "center")
        r = etree.SubElement(p, qn("w:r"))
        r.append(_delegated_header_rpr())
        t = etree.SubElement(r, qn("w:t"))
        t.text = label
    return tbl


def _insert_delegated_tasks_page(
    body: etree._Element,
) -> tuple[etree._Element, etree._Element, etree._Element]:
    """Create a fresh, empty delegated-tasks page (calendar header + spacer +
    table-with-header-row-only, followed by a forced page break) and splice
    it in right before the meeting section — where the template's own
    delegated page lives — returning its (header, spacer, table) triple. The
    calendar header is cloned from meeting page 1's own header (always
    present, identical shape) rather than authored from scratch, same as
    every other cloned header in this module. The trailing break is always
    freshly created here (never reused) — remove_delegated_tasks_page always
    removes it along with the page, since without it, whatever follows would
    just continue on the same page as this one, which is much shorter than a
    full page."""
    meeting_header, _, _ = find_meeting_unit_template(body)
    header = copy.deepcopy(meeting_header)
    spacer = etree.Element(qn("w:p"))
    table = _build_delegated_table_shell()
    meeting_header.addprevious(header)
    meeting_header.addprevious(spacer)
    meeting_header.addprevious(table)
    meeting_header.addprevious(_page_break_paragraph())
    return header, spacer, table


def _delegated_body_rpr() -> etree._Element:
    """Run properties for delegated-row body text, transcribed byte-for-byte
    from the template's own sample rows (Outfit ExtraLight, template accent
    color, 10pt) — rows are built programmatically since their count varies
    per date, so there's no single template row left in the saved doc to
    clone from once remove_delegated_tasks_page has run."""
    rpr = etree.Element(qn("w:rPr"))
    fonts = etree.SubElement(rpr, qn("w:rFonts"))
    fonts.set(qn("w:ascii"), "Outfit ExtraLight")
    fonts.set(qn("w:hAnsi"), "Outfit ExtraLight")
    color = etree.SubElement(rpr, qn("w:color"))
    color.set(qn("w:val"), "F95738")
    for tag in ("w:sz", "w:szCs"):
        sz = etree.SubElement(rpr, qn(tag))
        sz.set(qn("w:val"), "20")
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


def _delegated_row(marked: bool, top_sz: int, bottom_sz: int | None) -> etree._Element:
    tr = etree.Element(qn("w:tr"))
    for key in ("task", "owner", "status"):
        tr.append(
            _delegated_cell(
                _DELEGATED_COLUMN_WIDTHS[key], top_sz, bottom_sz, marked, center=(key == "owner")
            )
        )
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
    if lines:
        family, size = cell_run_font(tc)
        bullet_width = text_width_twips(DELEGATED_BULLET_PREFIX, family=family, size_half_points=size)
        width = cell_text_width_twips(tc) - bullet_width
        fitted = [
            fit_single_line(line, family=family, size_half_points=size, max_width_twips=width)
            for line in lines
        ]
        set_cell_text_lines(tc, [f"{DELEGATED_BULLET_PREFIX}{line}" for line in fitted])
    # A second, near-invisible paragraph whose only job is its own
    # before-spacing: reserves ~2cm of blank room under the status text for
    # a handwritten update, without the row growing extra ruled lines.
    notes_p = etree.SubElement(tc, qn("w:p"))
    pPr = etree.SubElement(notes_p, qn("w:pPr"))
    spacing = etree.SubElement(pPr, qn("w:spacing"))
    spacing.set(qn("w:before"), str(DELEGATED_NOTES_FREE_SPACE_TWIPS))
    rpr = etree.SubElement(pPr, qn("w:rPr"))
    sz = etree.SubElement(rpr, qn("w:sz"))
    sz.set(qn("w:val"), "12")
    szCs = etree.SubElement(rpr, qn("w:szCs"))
    szCs.set(qn("w:val"), "12")


def _fill_delegated_row(tr: etree._Element, task: dict) -> None:
    cells = tr.findall("w:tc", NS)
    _set_task_cadence_cell(cells[0], task["text"], task.get("cadence", "daily"))
    _set_owner_cell(cells[1], task.get("owner", ""))
    _set_status_cell(cells[2], task.get("status", ""))


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
    docx is the only state, per agenda_store's module docstring)."""
    tasks: list[dict] = []
    for table in find_delegated_tables(body):
        for tr in table.findall("w:tr", NS)[1:]:
            cells = tr.findall("w:tc", NS)
            lines = _paragraph_lines(cells[0].find("w:p", NS))
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
                for line in _paragraph_lines(cells[2].find("w:p", NS))
                if line.strip()
            ]

            tasks.append(
                {
                    "text": text,
                    "owner": cell_text(cells[1]),
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
        for table in tables:
            header, spacer, table = _delegated_page_unit(table)
            body.remove(table)
            body.remove(spacer)
            body.remove(header)
        return

    if tables:
        first_header, first_spacer, first_table = _delegated_page_unit(tables[0])
        tail_anchor = tables[-1].getnext()
        for extra_table in tables[1:]:
            header, spacer, table = _delegated_page_unit(extra_table)
            page_break = header.getprevious()
            body.remove(table)
            body.remove(spacer)
            body.remove(header)
            if page_break is not None and page_break.tag == qn("w:p") and any(
                b.get(qn("w:type")) == "page" for b in page_break.findall(".//w:br", NS)
            ):
                body.remove(page_break)
        for row in first_table.findall("w:tr", NS)[1:]:
            first_table.remove(row)
    else:
        first_header, first_spacer, first_table = _insert_delegated_tasks_page(body)
        tail_anchor = first_table.getnext()  # the break paragraph leading into meeting page 1

    current_table = first_table
    row_on_page = 0
    last_row: etree._Element | None = None
    for task in tasks:
        if row_on_page >= DELEGATED_ROWS_PER_PAGE:
            new_header = copy.deepcopy(first_header)
            new_spacer = copy.deepcopy(first_spacer)
            new_table = _build_delegated_table_shell()  # header-row-only shell to fill
            tail_anchor.addprevious(_page_break_paragraph())
            tail_anchor.addprevious(new_header)
            tail_anchor.addprevious(new_spacer)
            tail_anchor.addprevious(new_table)
            current_table = new_table
            row_on_page = 0

        top_sz = _THICK_BORDER_SZ if row_on_page == 0 else _THIN_BORDER_SZ
        tr = _delegated_row(bool(task.get("marked")), top_sz, _THIN_BORDER_SZ)
        _fill_delegated_row(tr, task)
        current_table.append(tr)
        last_row = tr
        row_on_page += 1

    if last_row is not None:
        _strip_row_bottom_border(last_row)
