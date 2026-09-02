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
from magenda.agenda_state import AgendaState, DelegatedTask, schedule_slot_ids
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
               size_half_points: int, align: str, theme: Theme, widen: bool = True) -> None:
    if not text:
        return
    from magenda.font_packs import FONT_PACKS
    from magenda.text_fit import text_width_twips

    scale = theme_mod.role_size_scale(theme)
    fontsize = (size_half_points / 2) * scale
    color = _hex_to_rgb(theme_mod.role_color(theme, role))
    fontfile = str(theme_mod.role_font_file(theme, weight))
    fontname = f"{theme.font_pack}_{weight}"

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
        family = FONT_PACKS[theme.font_pack]["weights"][weight]
        widest_line = max((text_width_twips(line, family=family, size_half_points=round(size_half_points * scale))
                            for line in text.split("\n")), default=0)
        needed_width = widest_line / 20 + 4  # twips -> points, plus a hair of padding
        if needed_width > rect.width:
            rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + needed_width, rect.y1)

    page.insert_textbox(
        rect, text, fontsize=fontsize, fontname=fontname, fontfile=fontfile, color=color,
        align=_ALIGN.get(align, pymupdf.TEXT_ALIGN_LEFT),
    )


def _draw_slot(page: pymupdf.Page, slot: Slot, text: str | None, theme: Theme) -> None:
    value = slot.text if slot.text is not None else text
    if not value:
        return
    _draw_text(page, _padded(slot.rect), value, role=slot.role, weight=slot.weight,
               size_half_points=slot.size_half_points, align=slot.align, theme=theme)


def _union(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


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

def _draw_overview(page: pymupdf.Page, manifest, state: AgendaState, theme: Theme) -> None:
    from magenda import calendar_math

    by_id = {s.id: s for s in manifest.page_slots["overview"]}

    # Static labels + next-four-weeks header letters (fixed text=... on the
    # slot itself).
    for slot in manifest.page_slots["overview"]:
        if slot.text is not None:
            _draw_slot(page, slot, None, theme)

    # to-do list -- walk fixed rows top-down, allocating `rows` consecutive
    # row slots per task (mirrors the pre-rewrite xml_ops.append_tasks).
    row = 0
    for task in state.todo_tasks:
        task_slot = by_id.get(f"todo.row.{row}.task")
        due_slot = by_id.get(f"todo.row.{row}.due")
        if task_slot is not None:
            rect = task_slot.rect
            for extra in range(1, task.rows):
                extra_slot = by_id.get(f"todo.row.{row + extra}.task")
                if extra_slot is not None:
                    rect = _union(rect, extra_slot.rect)
            _draw_text(page, _padded(rect), "\n".join(task.lines), role=task_slot.role,
                       weight=task_slot.weight, size_half_points=task.size_half_points,
                       align="left", theme=theme)
        if due_slot is not None and task.due:
            _draw_text(page, _padded(due_slot.rect), task.due, role=due_slot.role, weight=due_slot.weight,
                       size_half_points=due_slot.size_half_points, align="left", theme=theme)
        # A wrapped task spanning >1 row shares one ruled line, not one per
        # row: whiten the row-boundary line(s) it would otherwise cross
        # (text_fit already decided this many rows are needed for it).
        for extra in range(1, task.rows):
            top_slot = by_id.get(f"todo.row.{row + extra - 1}.task")
            bottom_slot = by_id.get(f"todo.row.{row + extra}.task")
            if top_slot is not None and bottom_slot is not None:
                boundary_y = (top_slot.rect[3] + bottom_slot.rect[1]) / 2
                band = pymupdf.Rect(top_slot.rect[0] - 2, boundary_y - 2, top_slot.rect[2] + 60, boundary_y + 2)
                page.draw_rect(band, color=None, fill=(1, 1, 1))
        row += task.rows

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


def _draw_delegated_page(page: pymupdf.Page, manifest, tasks: list, theme: Theme, *, is_last_page: bool = True) -> None:
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
        _draw_text(page, number_rect, str(i + 1), role="label", weight=LC.DELEGATED_ROW_NUMBER_FAMILY_WEIGHT,
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

    for i, page_tasks in enumerate(delegated_pages):
        _draw_delegated_page(out[1 + i], manifest, page_tasks, theme, is_last_page=(i == n_delegated - 1))

    meeting_start = 1 + n_delegated
    for i, title in enumerate(state.meetings):
        _draw_meeting(out[meeting_start + i], manifest, title, theme)

    _draw_further_notes(out[len(out) - 1], manifest, theme)

    pdf_bytes = out.tobytes()
    out.close()

    return pdf_links.add_navigation_links(pdf_bytes, state, n_delegated)
