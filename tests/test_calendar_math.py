import datetime

from magenda.calendar_math import header_fields, next_four_weeks, week_span


def test_week_span_matches_template_example():
    # The template's source example: 2026-05-19 is a Tuesday in CW 21, week Mon18-Sun24.
    span = week_span(datetime.date(2026, 5, 19))
    assert span.iso_week == 21
    assert [d.day for d in span.days] == [18, 19, 20, 21, 22, 23, 24]


def test_header_fields_matches_template_example():
    fields = header_fields(datetime.date(2026, 5, 19))
    assert fields["day"] == "19"
    assert fields["weekday_name"] == "TUESDAY"
    assert fields["cw"] == "CW 21"
    assert fields["month"] == "MAY"
    assert fields["year"] == "2026"
    assert fields["week_days"] == [18, 19, 20, 21, 22, 23, 24]


def test_next_four_weeks_matches_template_example():
    weeks = next_four_weeks(datetime.date(2026, 5, 19))
    assert [w.iso_week for w in weeks] == [21, 22, 23, 24]
    assert [d.day for d in weeks[0].days] == [18, 19, 20, 21, 22, 23, 24]
    assert [d.day for d in weeks[1].days] == [25, 26, 27, 28, 29, 30, 31]
    assert [d.day for d in weeks[2].days] == [1, 2, 3, 4, 5, 6, 7]
    assert [d.day for d in weeks[3].days] == [8, 9, 10, 11, 12, 13, 14]


def test_week_span_year_boundary():
    # Dec 31 2025 is a Wednesday, ISO week 1 of 2026 starts that Monday (Dec 29).
    span = week_span(datetime.date(2025, 12, 31))
    assert span.iso_year == 2026
    assert span.iso_week == 1
    assert span.days[0] == datetime.date(2025, 12, 29)


def test_next_four_weeks_crosses_year_boundary():
    weeks = next_four_weeks(datetime.date(2025, 12, 31))
    assert len(weeks) == 4
    assert weeks[0].days[0] == datetime.date(2025, 12, 29)
    assert weeks[3].days[-1] == datetime.date(2026, 1, 25)
