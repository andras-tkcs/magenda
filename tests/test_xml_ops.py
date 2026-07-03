import datetime

import pytest

from magenda import agenda_store, calendar_math, xml_ops
from magenda.xml_ops import MagendaError


def fresh_doc():
    doc = agenda_store.AgendaDocument.load(agenda_store.TEMPLATE_PATH)
    xml_ops.blank_meeting_title_slot(doc.body)
    xml_ops.strip_meeting_notes_footer(doc.body)
    xml_ops.ensure_further_notes_page_break(doc.body)
    return doc


def test_find_calendar_blocks_finds_three():
    # page-1 top, meeting-page top, and the closing page's own header (added
    # by ensure_further_notes_page_break). The meeting page's notes table
    # used to ship with a 4th, vestigial embedded calendar footer — see
    # strip_meeting_notes_footer.
    doc = fresh_doc()
    blocks = xml_ops.find_calendar_blocks(doc.body)
    assert len(blocks) == 3


def test_apply_calendar_block_updates_title_and_days():
    doc = fresh_doc()
    fields = calendar_math.header_fields(datetime.date(2026, 12, 25))
    for block in xml_ops.find_calendar_blocks(doc.body):
        xml_ops.apply_calendar_block(block, fields)
        cells = block.title_row.findall("w:tc", xml_ops.NS)
        assert xml_ops.cell_text(cells[0]) == "25 FRIDAY"
        assert xml_ops.cell_text(cells[3]) == "DECEMBER"
        assert xml_ops.cell_text(cells[4]) == "2026"


def test_append_tasks_fills_top_down_and_enforces_capacity():
    doc = fresh_doc()
    table = xml_ops.find_todo_table(doc.body)
    xml_ops.append_tasks(table, [{"text": "A", "due": "1"}, {"text": "B", "due": "2"}])
    rows = table.findall("w:tr", xml_ops.NS)[1:]
    assert xml_ops.cell_text(rows[0].findall("w:tc", xml_ops.NS)[1]) == "A"
    assert xml_ops.cell_text(rows[1].findall("w:tc", xml_ops.NS)[1]) == "B"

    with pytest.raises(MagendaError):
        xml_ops.append_tasks(table, [{"text": f"task{i}"} for i in range(20)])


def test_fill_schedule_entries_picks_row_by_minute():
    doc = fresh_doc()
    table = xml_ops.find_schedule_table(doc.body)
    xml_ops.fill_schedule_entries(
        table,
        [
            {"time": "09:00", "text": "Standup"},
            {"time": "10:30", "text": "Half-hour meeting"},
        ],
    )
    rows = table.findall("w:tr", xml_ops.NS)
    # 8am=rows[0:2], 9am=rows[2:4], 10am=rows[4:6]
    assert xml_ops.cell_text(rows[2].findall("w:tc", xml_ops.NS)[1]) == "Standup"
    assert xml_ops.cell_text(rows[3].findall("w:tc", xml_ops.NS)[1]) == ""
    assert xml_ops.cell_text(rows[4].findall("w:tc", xml_ops.NS)[1]) == ""
    assert xml_ops.cell_text(rows[5].findall("w:tc", xml_ops.NS)[1]) == "Half-hour meeting"


def test_fill_schedule_entries_rejects_bad_input():
    doc = fresh_doc()
    table = xml_ops.find_schedule_table(doc.body)
    xml_ops.fill_schedule_entries(table, [{"time": "09:00", "text": "Standup"}])

    with pytest.raises(MagendaError):
        xml_ops.fill_schedule_entries(table, [{"time": "not-a-time", "text": "nope"}])

    with pytest.raises(MagendaError):
        xml_ops.fill_schedule_entries(table, [{"time": "07:00", "text": "too early"}])

    with pytest.raises(MagendaError):
        xml_ops.fill_schedule_entries(table, [{"time": "09:00", "text": "x"}, {"time": "09:15", "text": "y"}])


def test_fill_schedule_entries_truncates_instead_of_wrapping():
    doc = fresh_doc()
    table = xml_ops.find_schedule_table(doc.body)
    long_text = "This is a very very very very long meeting title that will not fit on one line"
    xml_ops.fill_schedule_entries(table, [{"time": "09:00", "text": long_text}])
    rows = table.findall("w:tr", xml_ops.NS)
    result = xml_ops.cell_text(rows[2].findall("w:tc", xml_ops.NS)[1])
    assert result != long_text
    assert long_text.startswith(result)


def test_append_tasks_downsizes_then_wraps_long_text():
    doc = fresh_doc()
    table = xml_ops.find_todo_table(doc.body)
    long_text = (
        "It is a very long task which describes a whole story from A to Z "
        "to check whether it is visible"
    )
    xml_ops.append_tasks(table, [{"text": long_text}])
    rows = table.findall("w:tr", xml_ops.NS)[1:]
    cell = rows[0].findall("w:tc", xml_ops.NS)[1]

    p = cell.find("w:p", xml_ops.NS)
    runs = p.findall("w:r", xml_ops.NS)
    assert len(runs) > 1  # wrapped across multiple runs/lines

    # Text survives in full (never truncated), just split across lines.
    lines = [t.text or "" for t in p.findall("w:r/w:t", xml_ops.NS)]
    assert " ".join(lines) == long_text
    for run in runs:
        sz = run.find("w:rPr/w:sz", xml_ops.NS)
        assert sz.get(xml_ops.qn("w:val")) == str(xml_ops.TODO_TASK_MIN_FONT_SIZE)


def test_append_tasks_keeps_short_text_on_one_line_at_default_size():
    doc = fresh_doc()
    table = xml_ops.find_todo_table(doc.body)
    xml_ops.append_tasks(table, [{"text": "Short task"}])
    rows = table.findall("w:tr", xml_ops.NS)[1:]
    cell = rows[0].findall("w:tc", xml_ops.NS)[1]
    p = cell.find("w:p", xml_ops.NS)
    runs = p.findall("w:r", xml_ops.NS)
    assert len(runs) == 1
    assert xml_ops.cell_text(cell) == "Short task"


def test_set_meeting_title_truncates_instead_of_wrapping():
    doc = fresh_doc()
    _, title_para, _ = xml_ops.find_meeting_unit_template(doc.body)
    long_title = (
        "It is a test meeting with an extreme super long title to check whether it breaks"
    )
    xml_ops.set_meeting_title(title_para, long_title)
    result = xml_ops.meeting_title_text(title_para)
    assert result != long_title
    assert long_title.startswith(result)


def test_insert_meeting_page_fills_blank_slot_then_clones():
    doc = fresh_doc()
    body = doc.body
    xml_ops.insert_meeting_page(body, "First meeting")
    _, title_para, _ = xml_ops.find_meeting_unit_template(body)
    assert xml_ops.meeting_title_text(title_para) == "First meeting"

    before = len(body.findall(".//w:tbl", xml_ops.NS))
    xml_ops.insert_meeting_page(body, "Second meeting")
    after = len(body.findall(".//w:tbl", xml_ops.NS))
    assert after == before + 2  # new header table + new notes table

    titles = [
        xml_ops._paragraph_text(p)[len(xml_ops.MEETING_TITLE_PREFIX):]
        for p in body.findall("w:p", xml_ops.NS)
        if xml_ops._paragraph_text(p).startswith(xml_ops.MEETING_TITLE_PREFIX)
    ]
    assert titles == ["First meeting", "Second meeting"]


def test_insert_meeting_page_keeps_closing_page_last():
    """Regression test: the closing page's header+page-break (added once by
    ensure_further_notes_page_break) must stay the LAST thing before
    'Further notes', not get sandwiched between meetings added afterward."""
    doc = fresh_doc()
    body = doc.body
    xml_ops.insert_meeting_page(body, "First meeting")
    xml_ops.insert_meeting_page(body, "Second meeting")
    xml_ops.insert_meeting_page(body, "Third meeting")

    from lxml import etree

    children = list(body)
    meeting_title_indices = [
        i for i, el in enumerate(children)
        if el.tag == xml_ops.qn("w:p") and xml_ops._paragraph_text(el).startswith(xml_ops.MEETING_TITLE_PREFIX)
    ]
    further_notes_index = next(
        i for i, el in enumerate(children)
        if el.tag == xml_ops.qn("w:p") and xml_ops._paragraph_text(el).strip() == xml_ops.FURTHER_NOTES_TEXT
    )
    assert len(meeting_title_indices) == 3
    assert all(i < further_notes_index for i in meeting_title_indices)

    # exactly one calendar header directly precedes "Further notes" (no duplicate stacked headers)
    header = children[further_notes_index - 1]
    assert xml_ops._is_calendar_header_table(header)
    before_header = children[further_notes_index - 2]
    assert before_header.tag == xml_ops.qn("w:p")
    assert any(
        b.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type") == "page"
        for b in before_header.findall(".//w:br", xml_ops.NS)
    )


def test_no_adjacent_tables_between_meetings():
    """Regression test for the merge bug: two <w:tbl> elements placed
    directly adjacent (no paragraph between) get silently merged by
    Word/LibreOffice, which was dropping ruled-line borders and duplicating
    calendar headers visually."""
    doc = fresh_doc()
    body = doc.body
    xml_ops.insert_meeting_page(body, "First meeting")
    xml_ops.insert_meeting_page(body, "Second meeting")

    children = list(body)
    for prev, cur in zip(children, children[1:]):
        if prev.tag == xml_ops.qn("w:tbl") and cur.tag == xml_ops.qn("w:tbl"):
            pytest.fail("found two adjacent <w:tbl> elements with no separating paragraph")


def test_save_and_reload_roundtrips_valid_xml(tmp_path):
    doc = fresh_doc()
    fields = calendar_math.header_fields(datetime.date(2026, 3, 1))
    for block in xml_ops.find_calendar_blocks(doc.body):
        xml_ops.apply_calendar_block(block, fields)
    xml_ops.insert_meeting_page(doc.body, "Roundtrip check")

    out = tmp_path / "roundtrip.docx"
    doc.save(out)

    reloaded = agenda_store.AgendaDocument.load(out)
    _, title_para, _ = xml_ops.find_meeting_unit_template(reloaded.body)
    assert xml_ops.meeting_title_text(title_para) == "Roundtrip check"
