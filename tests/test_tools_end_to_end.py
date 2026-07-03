import shutil

import pytest

from magenda import agenda_store, tools
from magenda.xml_ops import MagendaError

pytestmark = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")


@pytest.fixture(autouse=True)
def isolated_agenda_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(agenda_store, "AGENDA_DIR", tmp_path)
    yield tmp_path


def test_full_agenda_lifecycle():
    date = "2026-08-14"  # a Friday

    tools.create_agenda(date)
    tools.add_tasks(date, [{"text": "Pack bags", "due": "08/13"}, {"text": "Confirm flight"}])
    tools.add_daily_schedule(date, [{"time": "09:00", "text": "Airport shuttle"}, {"time": "14:00", "text": "Check-in"}])
    tools.add_meeting(date, "Pre-trip sync")
    tools.add_meeting(date, "Client handoff")
    tools.adjust_dates(date)  # re-applied to the same date — should be a no-op resync

    result = tools.render_pdf(date)

    import fitz

    doc = fitz.open(result["path"])
    # overview (1) + meeting 1 (1) + meeting 2 (1) + closing (1). No trailing
    # blank page.
    assert len(doc) == 4

    full_text = "".join(page.get_text() for page in doc)
    assert "14 FRIDAY" in full_text
    assert "CW 33" in full_text
    assert "AUGUST" in full_text
    assert "2026" in full_text
    assert "Pack bags" in full_text
    assert "Confirm flight" in full_text
    assert "Airport shuttle" in full_text
    assert "Check-in" in full_text
    assert "Pre-trip sync" in full_text
    assert "Client handoff" in full_text
    assert "Further notes from today" in full_text

    # Regression: a meeting's own calendar-header block must never appear
    # twice in a row on the same page (the old adjacent-table-merge bug).
    for page in doc:
        assert page.get_text().count("14 FRIDAY") <= 1

    # Determinism: re-rendering without changes reproduces the same page count and text.
    result2 = tools.render_pdf(date)
    doc2 = fitz.open(result2["path"])
    assert len(doc2) == len(doc)
    assert "".join(page.get_text() for page in doc2) == full_text


def test_meetings_render_one_page_each():
    """Regression test: each add_meeting call used to leave a fully blank
    page behind it (the meeting notes table's vestigial embedded calendar
    footer, plus a hard-page-break/pagination interaction), so N meetings
    produced far more than N physical meeting pages. Each meeting must now
    render on exactly one page."""
    date = "2026-08-24"
    tools.create_agenda(date)
    tools.add_meeting(date, "First")
    tools.add_meeting(date, "Second")
    tools.add_meeting(date, "Third")

    import fitz

    result = tools.render_pdf(date)
    doc = fitz.open(result["path"])
    # overview (1) + 3 single-page meetings + closing (1). No trailing
    # blank page.
    assert len(doc) == 5

    meeting_pages = [i for i, page in enumerate(doc) if "Meeting title:" in page.get_text()]
    assert len(meeting_pages) == 3

    # Every page must carry real content — no stray blank pages wedged
    # between meetings, and no trailing blank page after the closing page.
    for page in doc:
        assert page.get_text().strip() != ""


def test_meeting_title_truncated_not_wrapped():
    date = "2026-08-25"
    tools.create_agenda(date)
    long_title = (
        "It is a test meeting with an extreme super long title to check whether it breaks"
    )
    tools.add_meeting(date, long_title)

    import fitz

    result = tools.render_pdf(date)
    doc = fitz.open(result["path"])
    full_text = "".join(page.get_text() for page in doc)
    assert long_title not in full_text  # truncated, not the full long title
    assert "Meeting title:" in full_text


def test_create_agenda_twice_errors():
    date = "2026-09-01"
    tools.create_agenda(date)
    with pytest.raises(MagendaError):
        tools.create_agenda(date)


def test_adjust_dates_without_create_errors():
    with pytest.raises(MagendaError):
        tools.adjust_dates("2026-09-02")


def test_add_tasks_capacity_error_reports_remaining():
    date = "2026-09-03"
    tools.create_agenda(date)
    with pytest.raises(MagendaError, match="18 free"):
        tools.add_tasks(date, [{"text": f"task {i}"} for i in range(19)])
