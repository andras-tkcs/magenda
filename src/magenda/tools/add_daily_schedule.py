from magenda import agenda_store, xml_ops
from magenda.tools._common import parse_date


def add_daily_schedule(date: str, entries: list[dict]) -> dict:
    """Fill slots in the page-1 daily schedule table (8am-6pm, two rows per
    hour: 00-29 minutes and 30-59 minutes). Each entry: {"time": "10:30",
    "text": "Standup"} (24-hour HH:MM). An entry lands on exactly one row,
    picked by its start time — it never spans multiple rows. Slots not
    mentioned are left untouched; calling this again can fill additional
    slots. Text that doesn't fit the row on one line is truncated."""
    d = parse_date(date)
    doc = agenda_store.load(d)
    body = doc.body

    table = xml_ops.find_schedule_table(body)
    xml_ops.fill_schedule_entries(table, entries)

    path = agenda_store.save(d, doc)
    return {"date": d.isoformat(), "path": str(path), "slots_filled": len(entries)}
