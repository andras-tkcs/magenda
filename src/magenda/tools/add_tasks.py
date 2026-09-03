from magenda import agenda_state, agenda_store
from magenda.tools._common import parse_date


def add_tasks(date: str, tasks: list[dict]) -> dict:
    """Append tasks to the page-1 to-do list, filling the first empty rows
    top-down. Each task: {"text": "...", "due": "07/05"}. Raises if there
    isn't enough free capacity (18 rows total)."""
    d = parse_date(date)
    state = agenda_store.load(d)
    agenda_state.add_todo_tasks(state, tasks)
    agenda_store.save(d, state)
    return {"date": d.isoformat(), "tasks_added": len(tasks)}
