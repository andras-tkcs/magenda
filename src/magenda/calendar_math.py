"""Pure date arithmetic for the agenda template. No XML, no I/O — unit-testable in isolation."""
from __future__ import annotations

import datetime
from dataclasses import dataclass

WEEKDAY_ABBR_3 = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
WEEKDAY_ABBR_1 = ["M", "T", "W", "T", "F", "S", "S"]
WEEKDAY_NAME = [
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
]
MONTH_NAME = [
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
]

# Saturday/Sunday columns (0-indexed Mon=0..Sun=6) are rendered in red in the template.
WEEKEND_INDICES = {5, 6}


@dataclass(frozen=True)
class WeekSpan:
    iso_year: int
    iso_week: int
    days: tuple[datetime.date, ...]  # Monday .. Sunday


def week_span(date: datetime.date) -> WeekSpan:
    """Return the Mon-Sun week containing `date`, with its ISO week number."""
    monday = date - datetime.timedelta(days=date.weekday())
    days = tuple(monday + datetime.timedelta(days=i) for i in range(7))
    iso_year, iso_week, _ = date.isocalendar()
    return WeekSpan(iso_year=iso_year, iso_week=iso_week, days=days)


def next_four_weeks(date: datetime.date) -> list[WeekSpan]:
    """The current week plus the following 3 weeks (4 total), matching the
    template's 'NEXT FOUR WEEKS' grid which starts on the same week as the header."""
    start_monday = date - datetime.timedelta(days=date.weekday())
    return [week_span(start_monday + datetime.timedelta(weeks=i)) for i in range(4)]


def header_fields(date: datetime.date) -> dict:
    """Fields needed to populate a calendar header block (day/weekday/CW/month/year)."""
    span = week_span(date)
    return {
        "day": str(date.day),
        "weekday_name": WEEKDAY_NAME[date.weekday()],
        "cw": f"CW {span.iso_week}",
        "month": MONTH_NAME[date.month - 1],
        "year": str(date.year),
        "week_days": [d.day for d in span.days],
    }
