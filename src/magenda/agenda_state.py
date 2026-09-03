"""AgendaState: the runtime's entire working-agenda representation.

Plain data -- no XML, no PDF, no LibreOffice. Every MCP tool mutates one
of these (see agenda_store.py for the per-date store); render_pdf hands
the finished state to pdf_assembler.assemble() to produce PDF bytes.

Text fitting (truncation, downsize-then-wrap) happens here, at mutation
time, against the template's own baseline (Outfit) font metrics -- the
same thing the pre-rewrite xml_ops.py did against the always-Outfit-named
working docx (see theme.py's docstring history). A later theme swap at
render time never has to re-wrap: font-pack certification
(scripts/certify_font_pack.py) guarantees a certified pack's glyphs are
never measurably wider than Outfit's at matching weight, so line breaks
decided here stay valid for whatever pack ends up drawn.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field

from magenda import layout_constants as LC
from magenda.errors import MagendaError
from magenda.font_packs import FONT_PACKS
from magenda.text_fit import fit_downsize_or_wrap, fit_single_line, text_line_height_twips, text_width_twips

_OUTFIT_WEIGHTS = FONT_PACKS["outfit"]["weights"]
_VALID_CADENCES = ("daily", "weekly", "monthly")


def _family(weight: str) -> str:
    return _OUTFIT_WEIGHTS[weight]


def _next_tab_stop(x_twips: float) -> float:
    return (int(x_twips // LC.DEFAULT_TAB_STOP_TWIPS) + 1) * LC.DEFAULT_TAB_STOP_TWIPS


# --------------------------------------------------------------------------
# To-do list
# --------------------------------------------------------------------------


@dataclass
class TodoTask:
    text: str
    due: str
    lines: list[str]
    size_half_points: int
    rows: int  # how many of the 18 fixed rows this task occupies (vMerge'd if > 1)


def _fit_todo_task(text: str, due: str) -> TodoTask:
    family = _family(LC.TODO_TASK_FAMILY_WEIGHT)
    width = LC.TODO_TASK_CELL_WIDTH_TWIPS - 2 * LC.DEFAULT_CELL_MARGIN_TWIPS
    lines, size = fit_downsize_or_wrap(
        text,
        family=family,
        max_size_half_points=LC.TODO_TASK_MAX_FONT_SIZE,
        min_size_half_points=LC.TODO_TASK_MIN_FONT_SIZE,
        max_width_twips=width,
    )
    line_height = text_line_height_twips(family, size)
    rows = max(1, math.ceil(len(lines) * line_height / LC.TODO_ROW_HEIGHT_TWIPS))
    return TodoTask(text=text, due=due, lines=lines, size_half_points=size, rows=rows)


def add_todo_tasks(state: "AgendaState", tasks: list[dict]) -> None:
    """Append `tasks` ({"text", "due"}) to the to-do list, filling empty
    rows top-down. Raises if there isn't enough of the 18-row capacity
    left -- matches xml_ops.append_tasks's original error message shape,
    tests key off "N free" appearing in it."""
    free = LC.TODO_ROW_CAPACITY - sum(t.rows for t in state.todo_tasks)
    fitted = [_fit_todo_task(t["text"], t.get("due", "")) for t in tasks]
    needed = sum(t.rows for t in fitted)
    if needed > free:
        raise MagendaError(
            f"only {free} free to-do row(s) left (capacity {LC.TODO_ROW_CAPACITY}), "
            f"need {needed} row(s) for {len(tasks)} task(s)"
        )
    state.todo_tasks.extend(fitted)


# --------------------------------------------------------------------------
# Daily schedule
# --------------------------------------------------------------------------


@dataclass
class ScheduleEntry:
    text: str  # already truncated to fit


def _hour_label(hour24: int) -> str:
    if hour24 == 12:
        return "12pm"
    if hour24 > 12:
        return f"{hour24 - 12}pm"
    return f"{hour24}am"


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise MagendaError(f"time must be in 24-hour HH:MM format, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise MagendaError(f"time out of range: {value!r}")
    return hour, minute


def _fit_schedule_text(text: str) -> str:
    family = _family(LC.SCHEDULE_NOTES_FAMILY_WEIGHT)
    width = LC.SCHEDULE_NOTES_CELL_WIDTH_TWIPS - 2 * LC.DEFAULT_CELL_MARGIN_TWIPS
    return fit_single_line(text, family=family, size_half_points=LC.SCHEDULE_NOTES_FONT_SIZE, max_width_twips=width)


def set_schedule_entries(state: "AgendaState", entries: list[dict]) -> None:
    """Each entry: {"time": "HH:MM", "text": ...}. One entry lands on
    exactly one of the schedule's two rows per hour (00-29 -> first,
    30-59 -> second). Slots not mentioned are left untouched."""
    seen = set()
    for entry in entries:
        hour, minute = _parse_time(entry["time"])
        if not (LC.SCHEDULE_START_HOUR <= hour <= LC.SCHEDULE_END_HOUR):
            raise MagendaError(
                f"time {entry['time']!r} is outside the schedule's range "
                f"({_hour_label(LC.SCHEDULE_START_HOUR)}-{_hour_label(LC.SCHEDULE_END_HOUR)})"
            )
        half = 0 if minute < 30 else 1
        key = (hour, half)
        if key in seen:
            raise MagendaError(f"two entries both land on the {entry['time']!r} slot in this call")
        seen.add(key)
        state.schedule[key] = ScheduleEntry(text=_fit_schedule_text(entry["text"]))


def schedule_slot_ids() -> list[tuple[int, int]]:
    """Every (hour, half) slot key, in row order -- the same order the
    template's schedule table lists them in."""
    return [
        (hour, half)
        for hour in range(LC.SCHEDULE_START_HOUR, LC.SCHEDULE_END_HOUR + 1)
        for half in (0, 1)
    ]


# --------------------------------------------------------------------------
# Meeting pages
# --------------------------------------------------------------------------


def _fit_meeting_title(title: str) -> str:
    family = _family(LC.MEETING_TITLE_FAMILY_WEIGHT)
    label_width = text_width_twips(LC.MEETING_TITLE_PREFIX, family=family, size_half_points=LC.MEETING_TITLE_FONT_SIZE)
    x = _next_tab_stop(_next_tab_stop(label_width))
    return fit_single_line(
        title, family=family, size_half_points=LC.MEETING_TITLE_FONT_SIZE,
        max_width_twips=LC.PAGE_CONTENT_WIDTH_TWIPS - x,
    )


def add_meeting(state: "AgendaState", title: str) -> None:
    """Fill the first blank meeting slot (every agenda starts with exactly
    one, see AgendaState.create), or append a new one."""
    fitted = _fit_meeting_title(title)
    if state.meetings and state.meetings[0] == "":
        state.meetings[0] = fitted
    else:
        state.meetings.append(fitted)


# --------------------------------------------------------------------------
# Delegated tasks
# --------------------------------------------------------------------------


@dataclass
class DelegatedTask:
    text: str
    owner: str
    cadence: str
    status: str
    marked: bool
    task_lines: list[str] = field(default_factory=list)  # [cadence label, *wrapped task lines]
    task_size: int = LC.DELEGATED_TASK_MAX_FONT_SIZE
    owner_fitted: str = ""
    status_lines: list[str] = field(default_factory=list)  # already bulleted + fitted


def _fit_delegated(task: DelegatedTask) -> None:
    task_width = LC.DELEGATED_COLUMN_WIDTHS_TWIPS["task"] - 2 * LC.DEFAULT_CELL_MARGIN_TWIPS
    lines, size = fit_downsize_or_wrap(
        task.text,
        family=_family(LC.DELEGATED_TASK_FAMILY_WEIGHT),
        max_size_half_points=LC.DELEGATED_TASK_MAX_FONT_SIZE,
        min_size_half_points=LC.DELEGATED_TASK_MIN_FONT_SIZE,
        max_width_twips=task_width,
    )
    task.task_lines = [LC.DELEGATED_CADENCE_LABELS[task.cadence]] + lines
    task.task_size = size

    owner_width = LC.DELEGATED_COLUMN_WIDTHS_TWIPS["owner"] - 2 * LC.DEFAULT_CELL_MARGIN_TWIPS
    task.owner_fitted = (
        fit_single_line(
            task.owner, family=_family(LC.DELEGATED_TASK_FAMILY_WEIGHT),
            size_half_points=LC.DELEGATED_OWNER_FONT_SIZE, max_width_twips=owner_width,
        )
        if task.owner else ""
    )

    raw_lines = [line.strip() for line in task.status.split("\n") if line.strip()] if task.status else []
    status_width = LC.DELEGATED_COLUMN_WIDTHS_TWIPS["status"] - 2 * LC.DEFAULT_CELL_MARGIN_TWIPS
    bullet_width = text_width_twips(
        LC.DELEGATED_BULLET_PREFIX, family=_family(LC.DELEGATED_TASK_FAMILY_WEIGHT),
        size_half_points=LC.DELEGATED_STATUS_FONT_SIZE,
    )
    fitted = [
        fit_single_line(
            line, family=_family(LC.DELEGATED_TASK_FAMILY_WEIGHT),
            size_half_points=LC.DELEGATED_STATUS_FONT_SIZE, max_width_twips=status_width - bullet_width,
        )
        for line in raw_lines
    ]
    task.status_lines = [f"{LC.DELEGATED_BULLET_PREFIX}{line}" for line in fitted]


def add_delegated_tasks(state: "AgendaState", tasks: list[dict]) -> int:
    """Validate and merge `tasks` into the existing delegated-task set,
    re-sort the full combined list (marked first, then daily/weekly/monthly),
    and re-fit every row. Returns the number of newly added tasks."""
    new_tasks: list[DelegatedTask] = []
    for task in tasks:
        text = task.get("text", "").strip()
        if not text:
            raise MagendaError("each delegated task needs non-empty 'text'")
        cadence = task.get("cadence", "daily")
        if cadence not in _VALID_CADENCES:
            raise MagendaError(f"cadence must be one of {_VALID_CADENCES}, got {cadence!r}")
        new_tasks.append(
            DelegatedTask(
                text=text, owner=task.get("owner", ""), cadence=cadence,
                status=task.get("status", ""), marked=bool(task.get("marked", False)),
            )
        )

    combined = state.delegated_tasks + new_tasks
    combined.sort(key=lambda t: (0 if t.marked else 1, LC.DELEGATED_CADENCE_ORDER[t.cadence]))
    for task in combined:
        _fit_delegated(task)
    state.delegated_tasks = combined
    return len(new_tasks)


# --------------------------------------------------------------------------
# AgendaState itself
# --------------------------------------------------------------------------


@dataclass
class AgendaState:
    date: datetime.date
    todo_tasks: list[TodoTask] = field(default_factory=list)
    schedule: dict[tuple[int, int], ScheduleEntry] = field(default_factory=dict)
    # Every agenda starts with exactly one meeting slot, blank until the
    # first add_meeting call fills it in place -- matches the template's
    # own shipped shape (one example meeting page) and existing test
    # expectations (create_agenda alone already renders a meeting page).
    meetings: list[str] = field(default_factory=lambda: [""])
    delegated_tasks: list[DelegatedTask] = field(default_factory=list)

    @classmethod
    def create(cls, date: datetime.date) -> "AgendaState":
        return cls(date=date)

    def meeting_page_index(self, meeting_index: int, delegated_page_count: int) -> int:
        """0-indexed final-PDF page number of meeting `meeting_index`:
        overview (0), then one page per delegated-tasks page, then one
        page per meeting in order. `delegated_page_count` -- how many
        delegated-tasks pages the render actually used -- has to come from
        the caller: unlike everything else this state tracks, it depends
        on the active theme (a row's height, and so how many fit per page,
        varies with the font pack's line height -- see
        pdf_assembler._plan_delegated_pages), not on the state alone."""
        return 1 + delegated_page_count + meeting_index

    def read_daily_schedule_entries(self) -> list[str]:
        """Text of every filled schedule slot, in row (hour, half) order."""
        return [self.schedule[key].text for key in schedule_slot_ids() if key in self.schedule]

    def match_schedule_to_meetings(self) -> list[tuple[str, int]]:
        """Pair each filled schedule slot with the meeting it names, by
        (independently truncated) prefix match -- see the pre-rewrite
        xml_ops.match_schedule_to_meetings, same rule, same rationale."""
        pairs = []
        for text in self.read_daily_schedule_entries():
            matches = [
                i for i, title in enumerate(self.meetings)
                if title and (text.startswith(title) or title.startswith(text))
            ]
            if len(matches) == 1:
                pairs.append((text, matches[0]))
        return pairs
