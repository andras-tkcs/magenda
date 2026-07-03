from magenda import agenda_store, calendar_math, xml_ops
from magenda.tools._common import parse_date


def create_agenda(date: str) -> dict:
    """Create a fresh agenda for `date` from the template, with all calendar
    fields (header dates, next-4-weeks grid) already populated for that date."""
    d = parse_date(date)
    doc = agenda_store.create(d)
    body = doc.body

    fields = calendar_math.header_fields(d)
    for block in xml_ops.find_calendar_blocks(body):
        xml_ops.apply_calendar_block(block, fields)

    n4w_table = xml_ops.find_next_four_weeks_table(body)
    xml_ops.apply_next_four_weeks(n4w_table, calendar_math.next_four_weeks(d))

    path = agenda_store.save(d, doc)
    return {"date": d.isoformat(), "path": str(path)}
