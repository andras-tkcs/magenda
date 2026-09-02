from magenda import agenda_store
from magenda.tools._common import parse_date


def adjust_dates(date: str) -> dict:
    """Confirms the calendar header block and 'NEXT FOUR WEEKS' grid for
    `date`'s agenda are in sync. There's nothing to actually recompute any
    more: both are derived live from the agenda's own date at render time
    (see pdf_assembler._draw_header/_draw_overview), never stored, so they
    can't go stale between calls the way the pre-rewrite docx-based
    implementation's baked-in copies could. Kept as a tool mainly so
    existing callers/tests that call it after other edits keep working;
    it just confirms the agenda exists."""
    d = parse_date(date)
    agenda_store.load(d)
    return {"date": d.isoformat(), "calendar_blocks_updated": 1}
