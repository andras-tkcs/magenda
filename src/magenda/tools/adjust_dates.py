from magenda import agenda_store, calendar_math, xml_ops
from magenda.tools._common import parse_date


def adjust_dates(date: str) -> dict:
    """Regenerate every calendar header/footer block (top of every page, plus
    the embedded footer calendar on each meeting page) and the 'NEXT FOUR
    WEEKS' grid on page 1, for the agenda already on disk for `date`."""
    d = parse_date(date)
    doc = agenda_store.load(d)
    body = doc.body

    fields = calendar_math.header_fields(d)
    blocks = xml_ops.find_calendar_blocks(body)
    for block in blocks:
        xml_ops.apply_calendar_block(block, fields)

    n4w_table = xml_ops.find_next_four_weeks_table(body)
    xml_ops.apply_next_four_weeks(n4w_table, calendar_math.next_four_weeks(d))

    path = agenda_store.save(d, doc)
    return {"date": d.isoformat(), "path": str(path), "calendar_blocks_updated": len(blocks)}
