from magenda import agenda_store, xml_ops
from magenda.tools._common import parse_date


def add_meeting(date: str, title: str) -> dict:
    """Fill the first blank meeting slot, or clone a new meeting page and
    append it before the closing 'Further notes' page, setting its title."""
    d = parse_date(date)
    doc = agenda_store.load(d)
    body = doc.body

    xml_ops.insert_meeting_page(body, title)

    path = agenda_store.save(d, doc)
    return {"date": d.isoformat(), "path": str(path), "title": title}
