from magenda import agenda_store, xml_ops
from magenda.tools._common import parse_date


def add_tasks(date: str, tasks: list[dict]) -> dict:
    """Append tasks to the page-1 to-do list, filling the first empty rows
    top-down. Each task: {"text": "...", "due": "07/05"}. Raises if there
    isn't enough free capacity (18 rows total)."""
    d = parse_date(date)
    doc = agenda_store.load(d)
    body = doc.body

    table = xml_ops.find_todo_table(body)
    xml_ops.append_tasks(table, tasks)

    path = agenda_store.save(d, doc)
    return {"date": d.isoformat(), "path": str(path), "tasks_added": len(tasks)}
