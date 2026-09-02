from magenda import agenda_state, agenda_store
from magenda.tools._common import parse_date


def add_meeting(date: str, title: str) -> dict:
    """Fill the first blank meeting slot, or append a new meeting page,
    setting its title."""
    d = parse_date(date)
    state = agenda_store.load(d)
    agenda_state.add_meeting(state, title)
    agenda_store.save(d, state)
    return {"date": d.isoformat(), "title": title}
