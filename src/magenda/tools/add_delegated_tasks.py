from magenda import agenda_state, agenda_store
from magenda.tools._common import parse_date


def add_delegated_tasks(date: str, tasks: list[dict]) -> dict:
    """Add rows to the delegated-tasks page(s). Each task: {"text": ...,
    "owner": "...", "cadence": "daily"|"weekly"|"monthly", "marked": bool,
    "status": "..."}. `owner`, "marked" and "status" are optional (owner/
    status default to blank, marked defaults to False). `owner` is centered
    in its column; `status` renders as a bullet list, one bullet per "\\n"-
    separated line.

    Merges `tasks` with whatever delegated tasks already exist, re-sorts
    the full combined list (marked rows first, then unmarked; within each
    group daily, then weekly, then monthly — ties keep their existing
    relative order), and re-fits every row so the ordering rule always
    holds and there is never a trailing empty row. Uses as many pages as
    the combined task count needs."""
    d = parse_date(date)
    state = agenda_store.load(d)
    added = agenda_state.add_delegated_tasks(state, tasks)
    agenda_store.save(d, state)
    return {"date": d.isoformat(), "tasks_added": added, "tasks_total": len(state.delegated_tasks)}
