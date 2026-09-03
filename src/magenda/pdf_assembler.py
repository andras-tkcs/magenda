"""Assemble a finished agenda PDF from an AgendaState, entirely in Python:
clone page shells out of the compiled bundle (compiled_template.py),
insert every slot's text with the active theme's font/color, draw the
delegated-tasks rows procedurally (their count is dynamic, so no chrome
page carries any), and add navigation links. No subprocess, no
LibreOffice, no OS-level font install -- every font is handed to pymupdf
as a file on every call (see theme.role_font_file).

This is the module render_pdf (tools/render.py) calls instead of shelling
out to soffice -- see docs/design/remove-libreoffice-runtime-dependency.md.
"""
from __future__ import annotations

import pymupdf

from magenda import compiled_template, layout_constants as LC, pdf_links, theme as theme_mod
from magenda.agenda_state import AgendaState, DelegatedTask, TodoTask, schedule_slot_ids
from magenda.slot_schema import Slot
from magenda.theme import Theme

# A little padding around a slot's tightly-cropped capture rect (see
# scripts/compile_template.py) so ascenders/descenders on freshly inserted
# text -- which won't always match the exact glyphs the rect was measured
# against -- don't get clipped by insert_textbox.
_PAD_TOP = 3.0
_PAD_BOTTOM = 4.0
_PAD_SIDE = 1.5

_ALIGN = {"left": pymupdf.TEXT_ALIGN_LEFT, "center": pymupdf.TEXT_ALIGN_CENTER}


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _padded(rect: tuple[float, float, float, float]) -> pymupdf.Rect:
    x0, y0, x1, y1 = rect
    return pymupdf.Rect(x0 - _PAD_SIDE, y0 - _PAD_TOP, x1 + _PAD_SIDE, y1 + _PAD_BOTTOM)


def _draw_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str, *, role: str, weight: str,
               size_half_points: int, align: str, theme: Theme, widen: bool = True, center: bool = True) -> None:
    if not text:
        return
    from magenda.font_packs import FONT_PACKS
    from magenda.text_fit import text_line_height_twips, text_width_twips

    scale = theme_mod.role_size_scale(theme)
    fontsize = (size_half_points / 2) * scale
    color = _hex_to_rgb(theme_mod.role_color(theme, role))
    fontfile = str(theme_mod.role_font_file(theme, weight))
    fontname = f"{theme.font_pack}_{weight}"
    family = FONT_PACKS[theme.font_pack]["weights"][weight]
    scaled_size_half_points = round(size_half_points * scale)

    if align == "left" and widen:
        # Most slot rects (scripts/compile_template.py) are cropped tight
        # around whatever stood in for the real content during the capture
        # render -- a short sentinel token for a dynamic slot, or (for
        # some static ones, e.g. the calendar header's own weekday-name
        # cells) a narrow numeral sentinel captured alongside the dynamic
        # fields sharing that row -- rarely the same width as what's
        # actually drawn here. Left-aligned text only needs its left edge
        # anchored correctly, so widen the box to fit the real text's own
        # measured width rather than risk insert_textbox silently dropping
        # it for not fitting the captured one.
        widest_line = max((text_width_twips(line, family=family, size_half_points=scaled_size_half_points)
                            for line in text.split("\n")), default=0)
        needed_width = widest_line / 20 + 4  # twips -> points, plus a hair of padding
        if needed_width > rect.width:
            rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + needed_width, rect.y1)

    # insert_textbox always starts its first line at the box's own top edge
    # and never centers vertically -- so any slack between a rect's height
    # and its actual content (the padding _padded adds around a tightly-
    # cropped capture rect; a delegated row's calibrated 2-line height
    # showing only 1 line; a union of several row slots for a wrapped
    # to-do task that didn't end up needing all of them) landed entirely
    # below the text, reading as everything pinned to the top of its own
    # box. Shift the box down by half that slack so the content lands
    # centered instead -- estimated from this role/weight/size's own real
    # line height (text_line_height_twips), which is close to but not
    # exactly insert_textbox's own internal metric, so the box keeps its
    # *full* original height (just relocated) rather than being shrunk to
    # the estimate: shrinking to a tight fit occasionally undershot
    # insert_textbox's real requirement by a fraction of a point, and it
    # silently drops text that doesn't fit rather than clipping it. A
    # shifted-but-not-shrunk box can end up with its bottom edge past the
    # original rect's own -- harmless, since insert_textbox only ever
    # draws the lines it's given and never pads or outlines the box itself.
    # `center=False` opts a caller out of this entirely -- used only where
    # a rect's "slack" isn't really slack at all: a to-do task spanning
    # several physical row slots is deliberately drawn top-anchored across
    # their union, one text line per row, so that the row-boundary erasure
    # right below (_find_ruled_line and its caller) -- which targets a
    # specific chrome ruled line, not wherever this function happens to
    # put a line of text -- still lands under the right line. Shifting
    # that block down would desync the two.
    line_height = text_line_height_twips(family, scaled_size_half_points) / 20
    if center:
        content_height = line_height * (text.count("\n") + 1)
        slack = rect.height - content_height
        if slack > 0:
            shift = slack / 2
            rect = pymupdf.Rect(rect.x0, rect.y0 + shift, rect.x1, rect.y1 + shift)
        elif slack < 0:
            # The box is shorter than its own content needs -- e.g. a
            # to-do task whose wrapped line count was only sized against
            # the fixed-height row it was allocated (see agenda_state's
            # _fit_todo_task), not against this tightly-cropped capture
            # rect's own much shorter height. insert_textbox drops text
            # it can't fit rather than clipping it, so a slot sized like
            # that would silently lose the whole task -- grow the box
            # down to the height the content actually needs, the same
            # safety valve `widen` above gives left-aligned text that
            # measures wider than its own captured rect. content_height is
            # only this estimate's own measure of insert_textbox's real
            # requirement (see the module docstring above), which can
            # undershoot it by a point or so -- pad by _PAD_BOTTOM again
            # (on top of what `rect` already carries from _padded) for the
            # same margin every other caller's bottom edge gets.
            rect = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + content_height + _PAD_BOTTOM)

    kwargs = dict(fontsize=fontsize, fontname=fontname, fontfile=fontfile, color=color,
                  align=_ALIGN.get(align, pymupdf.TEXT_ALIGN_LEFT))
    rc = page.insert_textbox(rect, text, **kwargs)
    # Both safety valves above (`widen`, and the vertical grow just above)
    # size the box from our own PIL-based estimate of what pymupdf's real
    # text-layout engine needs -- close, per their own comments, but never
    # guaranteed exact, and the gap between the two can be wider on some
    # platform/pymupdf-version combination than whatever margin either
    # estimate built in (observed in practice: a task that rendered in
    # full here still lost text -- silently, per insert_textbox's own
    # drop-don't-clip behavior -- on another build). rc is pymupdf's own
    # authoritative fit signal (negative = didn't fit), so trust it over
    # the estimate: keep growing the box on both axes and retrying until
    # it actually reports success, rather than a single guess that's
    # merely usually right.
    attempts = 0
    while rc < 0 and attempts < 8:
        rect = pymupdf.Rect(rect.x0, rect.y0, rect.x1 + 10, rect.y1 + line_height)
        rc = page.insert_textbox(rect, text, **kwargs)
        attempts += 1


def _draw_slot(page: pymupdf.Page, slot: Slot, text: str | None, theme: Theme) -> None:
    value = slot.text if slot.text is not None else text
    if not value:
        return
    _draw_text(page, _padded(slot.rect), value, role=slot.role, weight=slot.weight,
               size_half_points=slot.size_half_points, align=slot.align, theme=theme)


def _find_ruled_line(page: pymupdf.Page, x0: float, x1: float, y_near: float) -> dict | None:
    """The wide horizontal ruled line closest to `y_near` that overlaps
    [x0, x1] on `page`'s own already-drawn chrome content (queried fresh
    via get_drawings, not estimated) -- the full drawing dict (rect,
    stroke color, stroke width), not just a rect, so a caller redrawing
    or erasing it can match its real geometry and style exactly rather
    than guessing either (see _draw_todo_list, which uses one real
    to-do-grid line as the template for every ruled line it draws)."""
    best = None
    best_dist = None
    for d in page.get_drawings():
        if d["type"] != "s":
            continue
        r = d["rect"]
        if r.height > 0.5 or r.width < 50:
            continue  # not a wide horizontal ruled line
        if r.x1 < x0 or r.x0 > x1:
            continue
        dist = abs(r.y0 - y_near)
        if best_dist is None or dist < best_dist:
            best, best_dist = d, dist
    return best


def _find_checkbox(page: pymupdf.Page, x_max: float, y0: float, y1: float) -> dict | None:
    """The small square checkbox stroke -- baked into chrome, one per
    physical to-do row, see scripts/compile_template.py's checkbox
    handling -- closest to [y0, y1] whose bounding box sits left of
    `x_max`. The full drawing dict, for the same reason _find_ruled_line
    returns one: _draw_todo_list uses one real checkbox's own size,
    position, color and stroke width as the template for every checkbox
    it draws."""
    best = None
    best_dist = None
    for d in page.get_drawings():
        if d["type"] != "s":
            continue
        r = d["rect"]
        if r.width > 15 or r.height > 15 or r.width < 2 or r.height < 2:
            continue  # not a small square-ish glyph
        if r.x1 > x_max or not (y0 <= r.y0 <= y1):
            continue
        dist = abs((r.y0 + r.y1) / 2 - (y0 + y1) / 2)
        if best_dist is None or dist < best_dist:
            best, best_dist = d, dist
    return best


def _erase_checkboxes_near(page: pymupdf.Page, x_max: float, y0: float, y1: float) -> None:
    """Whiten every small square checkbox stroke -- baked into chrome, one
    per physical to-do row, see scripts/compile_template.py's checkbox
    handling -- whose bounding box sits left of `x_max` and within
    [y0, y1]. _draw_todo_list uses this to clear chrome's entire 18-row
    grid (every row's checkbox, whether or not this render ends up
    putting a task there) before redrawing the grid procedurally at
    whatever per-row height each task's own wrapped lines need."""
    for d in page.get_drawings():
        if d["type"] != "s":
            continue
        r = d["rect"]
        if r.width > 15 or r.height > 15 or r.width < 2 or r.height < 2:
            continue  # not a small square-ish glyph
        if r.x1 > x_max or not (y0 <= r.y0 <= y1):
            continue
        page.draw_rect(pymupdf.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1), color=None, fill=(1, 1, 1))


def _erase_ruled_lines(page: pymupdf.Page, x0: float, x1: float, y0: float, y1: float) -> None:
    """Whiten every wide horizontal ruled line -- baked into chrome, one
    per physical to-do row boundary -- overlapping [x0, x1] whose top
    edge falls within [y0, y1]. _draw_todo_list's counterpart to
    _erase_checkboxes_near: together they clear the whole chrome-baked
    18-row grid before redrawing it procedurally."""
    for d in page.get_drawings():
        if d["type"] != "s":
            continue
        r = d["rect"]
        if r.height > 0.5 or r.width < 50:
            continue  # not a wide horizontal ruled line
        if r.x1 < x0 or r.x0 > x1:
            continue
        if not (y0 <= r.y0 <= y1):
            continue
        page.draw_rect(pymupdf.Rect(r.x0 - 1, r.y0 - 1.5, r.x1 + 1, r.y0 + 1.5), color=None, fill=(1, 1, 1))


def _find_shaded_band_x(page: pymupdf.Page, x_near: float, y_near: float) -> tuple[float, float] | None:
    """The (x0, x1) of the non-white filled rectangle covering (`x_near`,
    `y_near`) -- used to center a header label (e.g. "TO-DO LIST") against
    its real shaded cell width. That cell's own slot rect is cropped tight
    around a short compile-time sentinel token that itself rendered
    centered in the cell (see build_todo_schedule_fixture), so its left
    edge isn't the cell's own left edge -- drawing the real, much wider
    label left-aligned from there (the usual "widen rightward" handling
    every other left-aligned slot gets) systematically overshoots to the
    right of true center instead. The cell's shading is untouched chrome
    (never part of any redaction rect), so it's a reliable width to
    measure against. Matched by actually containing (x_near, y_near), not
    just "widest at this y" -- the to-do and daily-schedule boxes sit on
    the same row, so the wrong one is as wide a candidate as the right
    one."""
    best = None
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is None or fill == (1, 1, 1):
            continue
        r = d.get("rect")
        if r is None or r.width < 50:
            continue
        if not (r.x0 - 2 <= x_near <= r.x1 + 2 and r.y0 - 2 <= y_near <= r.y1 + 2):
            continue
        if best is None or r.width > best.width:
            best = r
    return (best.x0, best.x1) if best is not None else None


# --------------------------------------------------------------------------
# Header (every physical page)
# --------------------------------------------------------------------------

def _header_values(state: AgendaState) -> dict[str, str]:
    from magenda import calendar_math
    fields = calendar_math.header_fields(state.date)
    values = {
        "header.heading": f"{fields['day']} {fields['weekday_name']}",
        "header.cw": fields["cw"],
        "header.month": fields["month"],
        "header.year": fields["year"],
    }
    for i, day in enumerate(fields["week_days"]):
        values[f"header.dayno.{i}"] = str(day)
    return values


def _draw_header(page: pymupdf.Page, manifest, values: dict[str, str], theme: Theme) -> None:
    for slot in manifest.header_slots:
        _draw_slot(page, slot, values.get(slot.id), theme)


# --------------------------------------------------------------------------
# Overview page: to-do list, daily schedule, next-four-weeks grid
# --------------------------------------------------------------------------

def _todo_row_height_pt(task: TodoTask, theme: Theme, base_row_height_pt: float) -> float:
    """Full row height (points) for one to-do task, given the chrome-
    baked grid's own calibrated single-row height (LC.TODO_ROW_HEIGHT_
    TWIPS) and the active theme's line height at this task's own fitted
    font size -- grows past the base only once this task's lines no
    longer fit the base row at that size. Mirrors _delegated_row_height's
    base+extra*line_height growth, except delegated's base always holds
    exactly 2 lines (its font size is fixed) while a to-do row's "how
    many lines fit before growing" floats with each task's own size
    (_fit_todo_task shrinks the font before it wraps at all): a min-size
    task already packs 2 lines into one base row; a max-size one only
    ever fit 1. This is exactly the threshold TodoTask.rows itself is
    computed against (see _fit_todo_task), so a task's drawn height here
    never exceeds what its rows-based capacity accounting already
    reserved for it."""
    line_height = _line_height_pt(LC.TODO_TASK_FAMILY_WEIGHT, task.size_half_points, theme)
    base_lines = max(1, int(base_row_height_pt // line_height))
    extra = max(0, len(task.lines) - base_lines)
    return base_row_height_pt + extra * line_height


def _draw_todo_list(page: pymupdf.Page, by_id: dict, state: AgendaState, theme: Theme) -> None:
    """Draw the to-do list as its own small procedural table -- each
    task's row grows to exactly the height its own wrapped lines need
    (_todo_row_height_pt, mirroring the delegated-tasks page's own
    _delegated_row_height/_draw_delegated_page) -- instead of being
    merged across a whole number of the chrome-baked table's fixed-
    height rows (scripts/compile_template.py bakes LC.TODO_ROW_CAPACITY
    of those, each LC.TODO_ROW_HEIGHT_TWIPS tall, complete with its own
    ruled line and checkbox -- see the pre-rewrite xml_ops.append_tasks
    this replaced). That fixed grid forced a wrapped task to consume a
    whole extra row even when its own lines needed only a little more
    room than one row already had, forced a merge hack that erased a
    chrome-baked line/checkbox per skipped row, and -- since a wrapped
    task's lines then had to be drawn one whole chunk per *physical* row
    rather than as a single block -- left its text sitting at the top of
    each of its rows instead of centered across the merged block as a
    whole.

    The chrome-baked grid itself isn't touched at compile time (its
    fixed size assumes every task fits in exactly one row), so this
    erases the whole grid's own ruled lines and checkboxes on every
    render (_erase_ruled_lines, _erase_checkboxes_near) and redraws both
    fresh, row by row, at whatever height each row's own content needs,
    followed by as many blank (fixed-height) rows as fit in whatever
    budget is left -- an empty checkbox row is itself the point of a
    printable to-do list (room to add a task by hand), not wasted space
    to reclaim. Every redrawn row's stroke color/width and the
    checkbox's own size/position are read back from one real row this
    page's chrome still carries (_find_ruled_line, _find_checkbox)
    rather than hardcoded, so this keeps matching chrome across a theme
    or template recompile without needing its own touch-up."""
    task0 = by_id.get("todo.row.0.task")
    due0 = by_id.get("todo.row.0.due")
    if task0 is None or due0 is None:
        return  # stale compiled bundle -- nothing to anchor the grid on

    base_row_height_pt = LC.TODO_ROW_HEIGHT_TWIPS / 20

    # Row 0 has no ruled line of its own above it (it sits directly under
    # the "Task"/"Due" column headers' own short underlines instead, not
    # a full-width row boundary) -- anchor its top by walking back one
    # row height from the real ruled line between row 0 and row 1, found
    # near row 0's own (tightly-cropped) capture rect.
    line = _find_ruled_line(page, task0.rect[0] - 40, due0.rect[2] + 40,
                             task0.rect[3] + (base_row_height_pt - (task0.rect[3] - task0.rect[1])))
    if line is None:
        return
    x0, x1 = line["rect"].x0, line["rect"].x1
    line_color, line_width = line.get("color") or (0, 0, 0), line.get("width") or 0.75
    row0_top = line["rect"].y0 - base_row_height_pt

    checkbox = _find_checkbox(page, task0.rect[0] - 2, task0.rect[1] - 12, task0.rect[3] + 12)
    if checkbox is None:
        return
    box_rect = checkbox["rect"]
    box_size = box_rect.width
    box_cx = (box_rect.x0 + box_rect.x1) / 2
    box_color, box_width = checkbox.get("color") or (0, 0, 0), checkbox.get("width") or 0.75

    grid_bottom = row0_top + LC.TODO_ROW_CAPACITY * base_row_height_pt

    # Clear the whole chrome-baked grid up front, before drawing anything
    # of our own -- text is always the topmost thing on the page this
    # way, so it can never end up with a sliver of its own glyphs erased
    # by an estimate that landed a little too close (a real risk once
    # erasure ran last: a font pack whose ascent metrics differ from
    # Outfit's, the pack this grid's geometry was captured against, can
    # render a row's own first line closer to its erased top boundary
    # than Outfit does).
    _erase_ruled_lines(page, x0 - 1, x1 + 1, row0_top + 1, grid_bottom)
    _erase_checkboxes_near(page, task0.rect[0] - 2, row0_top - 2, grid_bottom + 2)

    def draw_row(y: float, row_height: float, is_first: bool, task: TodoTask | None) -> None:
        if not is_first:
            page.draw_line((x0, y), (x1, y), color=line_color, width=line_width)
        cy = y + row_height / 2
        page.draw_rect(pymupdf.Rect(box_cx - box_size / 2, cy - box_size / 2,
                                     box_cx + box_size / 2, cy + box_size / 2),
                        color=box_color, fill=None, width=box_width)
        if task is None:
            return
        task_rect = pymupdf.Rect(task0.rect[0] - _PAD_SIDE, y, due0.rect[0] - 4, y + row_height)
        _draw_text(page, task_rect, "\n".join(task.lines), role=task0.role, weight=task0.weight,
                   size_half_points=task.size_half_points, align="left", theme=theme)
        if task.due:
            due_rect = pymupdf.Rect(due0.rect[0] - _PAD_SIDE, y, x1 - 4, y + row_height)
            _draw_text(page, due_rect, task.due, role=due0.role, weight=due0.weight,
                       size_half_points=due0.size_half_points, align="left", theme=theme)

    y = row0_top
    is_first = True
    for task in state.todo_tasks:
        row_height = _todo_row_height_pt(task, theme, base_row_height_pt)
        draw_row(y, row_height, is_first=is_first, task=task)
        y += row_height
        is_first = False

    while y + base_row_height_pt <= grid_bottom + 0.5:
        draw_row(y, base_row_height_pt, is_first=is_first, task=None)
        y += base_row_height_pt
        is_first = False


def _draw_overview(page: pymupdf.Page, manifest, state: AgendaState, theme: Theme) -> None:
    from magenda import calendar_math

    by_id = {s.id: s for s in manifest.page_slots["overview"]}

    # Static labels + next-four-weeks header letters (fixed text=... on the
    # slot itself). "TO-DO LIST"/"DAILY SCHEDULE" are centered in their own
    # shaded header box in the template (w:jc center) -- draw them against
    # that box's real width (_find_shaded_band_x) rather than the usual
    # left-anchor-and-widen-rightward handling every other slot gets, which
    # only looks right for a slot whose captured rect already sits at its
    # cell's true left edge.
    for slot in manifest.page_slots["overview"]:
        if slot.text is None:
            continue
        if slot.id in ("todo.label", "schedule.label"):
            x_mid = (slot.rect[0] + slot.rect[2]) / 2
            y_mid = (slot.rect[1] + slot.rect[3]) / 2
            band_x = _find_shaded_band_x(page, x_mid, y_mid)
            if band_x is not None:
                rect = pymupdf.Rect(band_x[0], slot.rect[1] - _PAD_TOP, band_x[1], slot.rect[3] + _PAD_BOTTOM)
                _draw_text(page, rect, slot.text, role=slot.role, weight=slot.weight,
                           size_half_points=slot.size_half_points, align="center", theme=theme, widen=False)
                continue
        _draw_slot(page, slot, None, theme)

    _draw_todo_list(page, by_id, state, theme)

    # daily schedule
    for i, key in enumerate(schedule_slot_ids()):
        entry = state.schedule.get(key)
        if entry is None:
            continue
        slot = by_id.get(f"schedule.slot.{i}")
        if slot is not None:
            _draw_slot(page, slot, entry.text, theme)

    # next four weeks grid
    weeks = calendar_math.next_four_weeks(state.date)
    for wk, week in enumerate(weeks):
        cw_slot = by_id.get(f"next4weeks.week.{wk}.cw")
        if cw_slot is not None:
            _draw_slot(page, cw_slot, f"CW {week.iso_week}", theme)
        for i, day in enumerate(week.days):
            day_slot = by_id.get(f"next4weeks.week.{wk}.day.{i}")
            if day_slot is not None:
                _draw_slot(page, day_slot, str(day.day), theme)


# --------------------------------------------------------------------------
# Meeting pages
# --------------------------------------------------------------------------

def _draw_meeting(page: pymupdf.Page, manifest, title: str, theme: Theme) -> None:
    for slot in manifest.page_slots["meeting_unit"]:
        _draw_slot(page, slot, title, theme)


# --------------------------------------------------------------------------
# Further notes (closing page)
# --------------------------------------------------------------------------

def _draw_further_notes(page: pymupdf.Page, manifest, theme: Theme) -> None:
    for slot in manifest.page_slots["further_notes"]:
        _draw_slot(page, slot, None, theme)


# --------------------------------------------------------------------------
# Delegated tasks -- drawn procedurally (row count is dynamic, so no chrome
# page carries any rows -- see scripts/compile_template.py's docstring).
# --------------------------------------------------------------------------

_DELEGATED_HEADER_LABELS = {
    "delegated.header.task": "Task & cadence",
    "delegated.header.owner": "Owner",
    "delegated.header.status": "Status",
}


def _draw_delegated_page(page: pymupdf.Page, manifest, tasks: list, theme: Theme, *,
                          is_last_page: bool = True, start_number: int = 1) -> None:
    """Draw one delegated-tasks page. `start_number` is the row number the
    first row on this page should carry -- numbering continues across
    pages rather than restarting at 1 each time (the caller, assemble(),
    tracks the running total across every page it draws)."""
    geom = manifest.delegated
    x0 = geom.table_top_left[0]
    y = geom.table_top_left[1]
    col_x = {}
    cursor = x0
    for key in LC.DELEGATED_COLUMN_ORDER:
        col_x[key] = cursor
        cursor += LC.DELEGATED_COLUMN_WIDTHS_TWIPS[key] / 20
    table_right = cursor
    thick = LC.DELEGATED_THICK_BORDER_PT
    thin = LC.DELEGATED_THIN_BORDER_PT
    body_family_weight = LC.DELEGATED_TASK_FAMILY_WEIGHT

    # Header row labels ("Task & cadence"/"Owner"/"Status") are center-
    # aligned within their own (roomy) column, but their slot rect is
    # cropped tight around a short capture-time sentinel -- center
    # alignment can't widen-to-fit at draw time the way left-aligned text
    # does (there's no single edge to grow from), so these are drawn
    # against the column's own known full width instead of their slot
    # rect. Only the Y-range (line height, same regardless of column
    # width) comes from the slot.
    by_id = {s.id: s for s in manifest.page_slots["delegated_shell"]}
    for key, col_key in (("delegated.header.task", "task"), ("delegated.header.owner", "owner"),
                         ("delegated.header.status", "status")):
        slot = by_id.get(key)
        if slot is None:
            continue
        rect = pymupdf.Rect(col_x[col_key], slot.rect[1] - _PAD_TOP,
                             col_x[col_key] + LC.DELEGATED_COLUMN_WIDTHS_TWIPS[col_key] / 20, slot.rect[3] + _PAD_BOTTOM)
        _draw_text(page, rect, _DELEGATED_HEADER_LABELS[key], role=slot.role, weight=slot.weight,
                   size_half_points=slot.size_half_points, align="center", theme=theme, widen=False)
    for slot in manifest.page_slots["delegated_shell"]:
        if slot.id not in _DELEGATED_HEADER_LABELS:
            _draw_slot(page, slot, None, theme)
    line_height = _line_height_pt(body_family_weight, LC.DELEGATED_TASK_MAX_FONT_SIZE, theme)
    base_row_height = geom.row_overhead_twips / 20

    for i, task in enumerate(tasks):
        row_height = _delegated_row_height(task, base_row_height, line_height)
        border_sz = thick if i == 0 else thin
        # top border
        page.draw_line((x0, y), (table_right, y), color=(0, 0, 0), width=border_sz)
        row_rect_bottom = y + row_height
        if task.marked:
            page.draw_rect(pymupdf.Rect(x0, y, table_right, row_rect_bottom),
                            color=None, fill=_hex_to_rgb(LC.DELEGATED_MARK_FILL))
            page.draw_line((x0, y), (table_right, y), color=(0, 0, 0), width=border_sz)  # redraw over fill

        number_rect = pymupdf.Rect(col_x["number"], y, col_x["task"], row_rect_bottom)
        _draw_text(page, number_rect, str(start_number + i), role="label", weight=LC.DELEGATED_ROW_NUMBER_FAMILY_WEIGHT,
                   size_half_points=LC.DELEGATED_ROW_NUMBER_FONT_SIZE, align="center", theme=theme)

        task_rect = pymupdf.Rect(col_x["task"] + 2, y, col_x["owner"] - 2, row_rect_bottom)
        _draw_text(page, task_rect, "\n".join(task.task_lines), role="body", weight=body_family_weight,
                   size_half_points=task.task_size, align="left", theme=theme)

        owner_rect = pymupdf.Rect(col_x["owner"] + 2, y, col_x["status"] - 2, row_rect_bottom)
        _draw_text(page, owner_rect, task.owner_fitted, role="body", weight=body_family_weight,
                   size_half_points=LC.DELEGATED_OWNER_FONT_SIZE, align="center", theme=theme)

        status_rect = pymupdf.Rect(col_x["status"] + 2, y, table_right - 2, row_rect_bottom)
        _draw_text(page, status_rect, "\n".join(task.status_lines), role="body", weight=body_family_weight,
                   size_half_points=LC.DELEGATED_STATUS_FONT_SIZE, align="left", theme=theme)

        y = row_rect_bottom

    # Closing bottom border: the template's own convention (see the
    # pre-rewrite xml_ops._strip_row_bottom_border) is that only the very
    # last row of the very last page leaves its bottom edge open -- every
    # other page's table looks visually cut off without a line closing it,
    # since more rows continue overleaf.
    if not is_last_page:
        page.draw_line((x0, y), (table_right, y), color=(0, 0, 0), width=thin)


def _delegated_row_height(task, base_row_height: float, line_height: float) -> float:
    """Full row height (points) for one delegated task, given the
    calibrated 2-line base height (DelegatedGeometry.row_overhead_twips)
    and the active theme's line height -- grows past the base once the
    task or status cell wraps onto more than 2 lines. Shared between the
    pagination planner (_plan_delegated_pages) and the drawing loop
    (_draw_delegated_page) so the two can never disagree about how tall a
    row is -- which is exactly what would let a row be planned onto one
    page but actually get drawn spilling onto the next."""
    content_lines = max(len(task.task_lines), len(task.status_lines) or 1, 1)
    extra = max(0, content_lines - 2)
    return base_row_height + extra * line_height


def _delegated_max_y(manifest) -> float:
    """The lowest Y a delegated-tasks row may extend to before it would
    run into the "Notes and updates" footer label below the table (see
    scripts/compile_template.py's build_delegated_fixture) -- the actual
    page-break boundary _plan_delegated_pages fits rows against. Falls
    back to a generic page-bottom margin if the manifest doesn't carry
    that slot (e.g. an older compiled bundle)."""
    by_id = {s.id: s for s in manifest.page_slots.get("delegated_shell", [])}
    footer = by_id.get("delegated.footer_label")
    if footer is not None:
        return footer.rect[1] - LC.DELEGATED_TABLE_BOTTOM_GAP_PT
    return manifest.page_height - 72  # 1" fallback margin


def _plan_delegated_pages(tasks: list[DelegatedTask], manifest, theme: Theme) -> list[list]:
    """Group `tasks` into delegated-tasks pages, never splitting a row
    across two pages: a row that wouldn't fully fit in the space left on
    the current page starts a fresh page instead of overflowing into it
    (mirrors Word's "keep row together" table option, which the
    pre-rewrite docx-based renderer got for free from the OOXML table
    layout engine and this procedural one has to reproduce by hand). This
    replaces the old fixed DELEGATED_ROWS_PER_PAGE-per-page count, which
    ignored actual row heights entirely -- a handful of tall wrapped rows
    could overflow a page's printable area, and a run of short ones left
    a page under-full. A single row taller than an entire empty page still
    gets a page of its own rather than being silently dropped."""
    if not tasks:
        return []
    geom = manifest.delegated
    base_row_height = geom.row_overhead_twips / 20
    line_height = _line_height_pt(LC.DELEGATED_TASK_FAMILY_WEIGHT, LC.DELEGATED_TASK_MAX_FONT_SIZE, theme)
    max_y = _delegated_max_y(manifest)
    top_y = geom.table_top_left[1]

    pages: list[list] = []
    current: list = []
    y = top_y
    for task in tasks:
        row_height = _delegated_row_height(task, base_row_height, line_height)
        if current and y + row_height > max_y:
            pages.append(current)
            current = []
            y = top_y
        current.append(task)
        y += row_height
    if current:
        pages.append(current)
    return pages


def _line_height_pt(weight: str, size_half_points: int, theme: Theme) -> float:
    """Estimated line height (points) for `weight` at `size_half_points`
    under the active theme -- used to grow a delegated-task row past its
    calibrated 2-line base height (see DelegatedGeometry.row_overhead_twips)
    for extra wrapped/status lines. Resolved from the active pack (not
    always Outfit) so a wider/taller pack gets a taller estimate too."""
    from magenda.font_packs import FONT_PACKS
    from magenda.text_fit import text_line_height_twips
    family = FONT_PACKS[theme.font_pack]["weights"][weight]
    scale = theme_mod.role_size_scale(theme)
    return text_line_height_twips(family, round(size_half_points * scale)) / 20


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def assemble(state: AgendaState, theme: Theme) -> bytes:
    ct = compiled_template.load()
    manifest = ct.manifest
    chrome_src = pymupdf.open(stream=ct.chrome_bytes, filetype="pdf")

    delegated_pages = _plan_delegated_pages(state.delegated_tasks, manifest, theme)
    n_delegated = len(delegated_pages)
    plan = ["overview"] + ["delegated_shell"] * n_delegated + ["meeting_unit"] * len(state.meetings) + ["further_notes"]

    out = pymupdf.open()
    for role in plan:
        idx = manifest.chrome_pages[role]
        out.insert_pdf(chrome_src, from_page=idx, to_page=idx)
    chrome_src.close()

    header_values = _header_values(state)
    for page in out:
        _draw_header(page, manifest, header_values, theme)

    _draw_overview(out[0], manifest, state, theme)

    row_number = 1
    for i, page_tasks in enumerate(delegated_pages):
        _draw_delegated_page(out[1 + i], manifest, page_tasks, theme,
                              is_last_page=(i == n_delegated - 1), start_number=row_number)
        row_number += len(page_tasks)

    meeting_start = 1 + n_delegated
    for i, title in enumerate(state.meetings):
        _draw_meeting(out[meeting_start + i], manifest, title, theme)

    _draw_further_notes(out[len(out) - 1], manifest, theme)

    pdf_bytes = out.tobytes()
    out.close()

    return pdf_links.add_navigation_links(pdf_bytes, state, n_delegated)
