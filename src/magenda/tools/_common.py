import datetime

from magenda.errors import MagendaError


def parse_date(date: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(date)
    except ValueError:
        raise MagendaError(f"date must be in YYYY-MM-DD format, got {date!r}") from None
