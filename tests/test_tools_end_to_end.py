import base64
import shutil

import pytest

from magenda import tools
from magenda.xml_ops import MagendaError

pytestmark = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")


def _open_pdf(result: dict):
    """Open a render_pdf() result regardless of whether it kept a path
    (output_dir was given) or came back as base64 only (the default,
    no-disk-footprint case)."""
    import fitz

    if "path" in result:
        return fitz.open(result["path"])
    return fitz.open(stream=base64.b64decode(result["pdf_base64"]), filetype="pdf")


def test_full_agenda_lifecycle(tmp_path):
    date = "2026-08-14"  # a Friday

    tools.create_agenda(date)
    tools.add_tasks(date, [{"text": "Pack bags", "due": "08/13"}, {"text": "Confirm flight"}])
    tools.add_daily_schedule(date, [{"time": "09:00", "text": "Airport shuttle"}, {"time": "14:00", "text": "Check-in"}])
    tools.add_meeting(date, "Pre-trip sync")
    tools.add_meeting(date, "Client handoff")
    tools.adjust_dates(date)  # re-applied to the same date — should be a no-op resync

    result = tools.render_pdf(date, output_dir=str(tmp_path))

    doc = _open_pdf(result)
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

    # Determinism: re-rendering without changes reproduces the same page count
    # and text. This call also exercises the default no-disk-footprint path
    # (no output_dir): the result carries pdf_base64 instead of a path.
    result2 = tools.render_pdf(date)
    assert "path" not in result2
    assert "pdf_base64" in result2
    doc2 = _open_pdf(result2)
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

    result = tools.render_pdf(date)
    doc = _open_pdf(result)
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

    result = tools.render_pdf(date)
    doc = _open_pdf(result)
    full_text = "".join(page.get_text() for page in doc)
    assert long_title not in full_text  # truncated, not the full long title
    assert "Meeting title:" in full_text


def test_create_agenda_twice_starts_from_scratch():
    date = "2026-09-01"
    tools.create_agenda(date)
    tools.add_meeting(date, "Should be wiped")

    tools.create_agenda(date)

    result = tools.render_pdf(date)
    doc = _open_pdf(result)
    full_text = "".join(page.get_text() for page in doc)
    assert "Should be wiped" not in full_text


def test_adjust_dates_without_create_errors():
    with pytest.raises(MagendaError):
        tools.adjust_dates("2026-09-02")


def test_add_tasks_capacity_error_reports_remaining():
    date = "2026-09-03"
    tools.create_agenda(date)
    with pytest.raises(MagendaError, match="18 free"):
        tools.add_tasks(date, [{"text": f"task {i}"} for i in range(19)])


def test_agenda_has_no_delegated_page_by_default():
    date = "2026-09-04"
    tools.create_agenda(date)

    result = tools.render_pdf(date)
    doc = _open_pdf(result)
    # overview (1) + blank meeting-1 slot (1) + closing (1) — no
    # delegated-tasks page, no trailing blank page.
    assert len(doc) == 3
    full_text = "".join(page.get_text() for page in doc)
    assert "Task & cadence" not in full_text
    for page in doc:
        assert page.get_text().strip() != ""


def test_add_delegated_tasks_adds_the_page_back_and_orders_rows():
    date = "2026-09-05"
    tools.create_agenda(date)
    tools.add_delegated_tasks(
        date,
        [
            {"text": "Renew SSL certs", "owner": "Bence", "cadence": "monthly"},
            {"text": "Backup shared drive", "owner": "Kata", "cadence": "daily", "marked": True},
            {"text": "Water the plants", "owner": "Taki", "cadence": "daily"},
        ],
    )

    result = tools.render_pdf(date)
    doc = _open_pdf(result)
    # overview (1) + delegated (1) + blank meeting-1 slot (1) + closing (1).
    assert len(doc) == 4
    for page in doc:
        assert page.get_text().strip() != ""

    delegated_text = doc[1].get_text()
    assert "Task & cadence".upper() in delegated_text.upper()
    marked_idx = delegated_text.index("Backup shared drive")
    daily_idx = delegated_text.index("Water the plants")
    monthly_idx = delegated_text.index("Renew SSL certs")
    assert marked_idx < daily_idx < monthly_idx  # marked first, then daily before monthly


def test_add_delegated_tasks_merges_and_resorts_across_calls():
    date = "2026-09-06"
    tools.create_agenda(date)
    tools.add_delegated_tasks(date, [{"text": "First batch task", "cadence": "monthly"}])
    tools.add_delegated_tasks(
        date, [{"text": "Second batch, marked", "cadence": "weekly", "marked": True}]
    )

    result = tools.render_pdf(date)
    doc = _open_pdf(result)
    delegated_text = doc[1].get_text()
    # the second call's marked task now sorts ahead of the first call's unmarked one
    assert delegated_text.index("Second batch, marked") < delegated_text.index("First batch task")


def test_add_delegated_tasks_rejects_bad_cadence():
    date = "2026-09-07"
    tools.create_agenda(date)
    with pytest.raises(MagendaError):
        tools.add_delegated_tasks(date, [{"text": "Bad cadence", "cadence": "yearly"}])
