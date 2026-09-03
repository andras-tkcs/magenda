"""Numeric/structural constants describing the template's fixed layout --
row capacities, font-size defaults, table column widths, static label text.

Shared between the runtime (agenda_state.py, pdf_assembler.py) and the
one-off compiler (scripts/compile_template.py, which still edits real
OOXML via scripts/compiler/xml_ops.py): both need to agree on, e.g., "the
to-do list has 18 rows" or "a delegated task's cadence label renders at
9pt" without one side silently drifting from the other. Values here are
transcribed from the template's own OOXML (see assets/template.docx) --
the same numbers scripts/compiler/xml_ops.py used to hold as
runtime-and-compiler-both constants before the LibreOffice-removal
rewrite split runtime and compiler into separate code paths.
"""
from __future__ import annotations

# -- To-do list (page 1 left column) ---------------------------------------

TODO_ROW_CAPACITY = 18
TODO_TASK_MAX_FONT_SIZE = 24  # half-points (12pt) -- the template's own default
TODO_TASK_MIN_FONT_SIZE = 18  # half-points (9pt) -- floor before wrapping kicks in
TODO_TASK_FAMILY_WEIGHT = "thin"  # font_packs.py weight bucket
TODO_ROW_HEIGHT_TWIPS = 567  # fixed per-row height -- wrapped tasks vMerge across rows, never grow one
TODO_TASK_CELL_WIDTH_TWIPS = 2500
TODO_DUE_CELL_WIDTH_TWIPS = 1602

# -- Daily schedule (page 1 right column) -----------------------------------

SCHEDULE_START_HOUR = 8   # 8am
SCHEDULE_END_HOUR = 18    # 6pm, inclusive
SCHEDULE_NOTES_FONT_SIZE = 24  # half-points (12pt)
SCHEDULE_NOTES_FAMILY_WEIGHT = "thin"
SCHEDULE_ROW_HEIGHT_TWIPS = 567
SCHEDULE_NOTES_CELL_WIDTH_TWIPS = 4253

# -- Meeting pages ------------------------------------------------------

MEETING_TITLE_PREFIX = "Meeting title: "
MEETING_TITLE_FONT_SIZE = 40  # half-points (20pt)
MEETING_TITLE_FAMILY_WEIGHT = "extralight"
FURTHER_NOTES_TEXT = "Further notes from today"
FURTHER_NOTES_FONT_SIZE = 40
FURTHER_NOTES_FAMILY_WEIGHT = "extralight"

# From the template's fixed sectPr -- pgSz.w=11906, pgMar.left=1134,
# pgMar.right=567 twips -> 11906-1134-567=10205.
PAGE_CONTENT_WIDTH_TWIPS = 10205
DEFAULT_TAB_STOP_TWIPS = 720
DEFAULT_CELL_MARGIN_TWIPS = 108  # standard Word default left/right cell margin

# -- Calendar header block (Word header, repeats on every page) -------------

HEADING_FONT_SIZE = 36  # half-points (18pt) -- "19 TUESDAY"
HEADING_FAMILY_WEIGHT = "black"
HEADING_SUB_FONT_SIZE = 28  # half-points (14pt) -- "CW 21" / "MAY" / "2026"
HEADING_SUB_FAMILY_WEIGHT = "regular"
WEEKDAY_LABEL_FONT_SIZE = 20  # half-points (10pt) -- "MON".."SUN"
WEEKDAY_LABEL_FAMILY_WEIGHT = "regular"
OVERVIEW_LINK_LABEL = "<< Overview"
NOTES_LINK_LABEL = "Notes >>"

# -- "NEXT FOUR WEEKS" grid (page 1 only) ------------------------------------

NEXT_FOUR_WEEKS_FONT_SIZE = 20  # half-points (10pt)
NEXT_FOUR_WEEKS_FAMILY_WEIGHT = "thin"
WEEKDAY_HEADER_LETTERS = ("M", "T", "W", "T", "F", "S", "S")
WEEKEND_COLUMN_INDICES = (5, 6)  # 0-indexed Mon..Sun -- Sat/Sun

# -- Delegated tasks page(s) -------------------------------------------------

DELEGATED_HEADER_LABELS = ("", "Task & cadence", "Owner", "Status")
DELEGATED_HEADER_FONT_SIZE = 28  # half-points (14pt)
DELEGATED_HEADER_FAMILY_WEIGHT = "black"
DELEGATED_MARK_FILL = "D6FCEC"
DELEGATED_HEADER_FILL = "D9D9D9"
DELEGATED_CADENCE_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
DELEGATED_CADENCE_ORDER = {"daily": 0, "weekly": 1, "monthly": 2}
DELEGATED_TASK_MAX_FONT_SIZE = 22  # half-points (11pt)
DELEGATED_TASK_MIN_FONT_SIZE = 18  # half-points (9pt)
DELEGATED_TASK_FAMILY_WEIGHT = "extralight"
DELEGATED_CADENCE_FONT_SIZE = 18  # half-points (9pt) -- small label above the task text
DELEGATED_OWNER_FONT_SIZE = 22
DELEGATED_STATUS_FONT_SIZE = 22
DELEGATED_BULLET_PREFIX = "• "
DELEGATED_BULLET_CONTINUATION_INDENT = "  "  # same rendered width as DELEGATED_BULLET_PREFIX, for wrapped status lines
DELEGATED_ROWS_PER_PAGE = 8  # worst-case-calibrated estimate, see xml_ops.py history
DELEGATED_ROW_NUMBER_FONT_SIZE = 28
DELEGATED_ROW_NUMBER_FAMILY_WEIGHT = "black"

# Breathing room (points) kept between the last delegated-tasks row a page
# can fit and the "Notes and updates" footer label below the table -- see
# pdf_assembler._delegated_max_y.
DELEGATED_TABLE_BOTTOM_GAP_PT = 6

_THICK_BORDER_EIGHTH_PT = 24  # OOXML w:sz is in eighths of a point -> 3pt
_THIN_BORDER_EIGHTH_PT = 4  # -> 0.5pt
DELEGATED_THICK_BORDER_PT = _THICK_BORDER_EIGHTH_PT / 8
DELEGATED_THIN_BORDER_PT = _THIN_BORDER_EIGHTH_PT / 8

# twips -- transcribed from assets/template.docx's own delegated-tasks table
# (scripts/compiler/xml_ops.py's _DELEGATED_COLUMN_WIDTHS/_DELEGATED_COLUMN_ORDER).
DELEGATED_COLUMN_WIDTHS_TWIPS = {"number": 615, "task": 3464, "owner": 1689, "status": 4580}
DELEGATED_COLUMN_ORDER = ("number", "task", "owner", "status")

# Empirically calibrated (scripts/compile_template.py, see its
# "delegated row-height calibration" step): fixed per-row overhead --
# paragraph spacing-before plus the cell's own top/bottom breathing room --
# that a row's height needs on top of its own wrapped-line content height.
# A default here is used only if assets/compiled/slots.json (written by the
# calibration step) doesn't override it -- see compiled_template.py.
DELEGATED_ROW_OVERHEAD_TWIPS_DEFAULT = 300
