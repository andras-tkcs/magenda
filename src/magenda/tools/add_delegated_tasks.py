from magenda import agenda_store, xml_ops
from magenda.tools._common import parse_date
from magenda.xml_ops import MagendaError

_VALID_CADENCES = ("daily", "weekly", "monthly")


def add_delegated_tasks(date: str, tasks: list[dict]) -> dict:
    """Add rows to the delegated-tasks page(s). Each task: {"text": ...,
    "owner": "...", "cadence": "daily"|"weekly"|"monthly", "marked": bool,
    "status": "..."}. `owner`, "marked" and "status" are optional (owner/
    status default to blank, marked defaults to False). `owner` is centered
    in its column; `status` renders as a bullet list, one bullet per "\\n"-
    separated line.

    Reads back whatever delegated tasks already exist on the page(s), adds
    `tasks` to that set, re-sorts the full combined list (marked rows first,
    then unmarked; within each group daily, then weekly, then monthly — ties
    keep their existing relative order), and rebuilds the page(s) from
    scratch so the ordering rule always holds and there is never a trailing
    empty row. Uses as many pages as the combined task count needs."""
    d = parse_date(date)
    doc = agenda_store.load(d)
    body = doc.body

    new_tasks = []
    for task in tasks:
        text = task.get("text", "").strip()
        if not text:
            raise MagendaError("each delegated task needs non-empty 'text'")
        cadence = task.get("cadence", "daily")
        if cadence not in _VALID_CADENCES:
            raise MagendaError(f"cadence must be one of {_VALID_CADENCES}, got {cadence!r}")
        new_tasks.append(
            {
                "text": text,
                "owner": task.get("owner", ""),
                "cadence": cadence,
                "status": task.get("status", ""),
                "marked": bool(task.get("marked", False)),
            }
        )

    combined = xml_ops.read_delegated_tasks(body) + new_tasks
    combined.sort(
        key=lambda t: (0 if t["marked"] else 1, xml_ops.DELEGATED_CADENCE_ORDER[t["cadence"]])
    )

    xml_ops.rebuild_delegated_tasks(body, combined)

    agenda_store.save(d, doc)
    return {"date": d.isoformat(), "tasks_added": len(new_tasks), "tasks_total": len(combined)}
