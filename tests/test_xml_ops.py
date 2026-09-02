import datetime

import pytest

from magenda import agenda_store, calendar_math, xml_ops
from magenda.xml_ops import MagendaError


def fresh_doc():
    doc = agenda_store.AgendaDocument.load(agenda_store.TEMPLATE_PATH)
    xml_ops.blank_meeting_title_slot(doc.body)
    xml_ops.ensure_further_notes_page_break(doc.body)
    xml_ops.remove_delegated_tasks_page(doc.body)
    return doc


def fresh_doc_with_delegated_page():
    """Like fresh_doc, but keeps the template's own delegated-tasks page
    (still carrying its 4 illustrative sample rows) instead of dropping it —
    for tests that exercise the template's shape directly."""
    doc = agenda_store.AgendaDocument.load(agenda_store.TEMPLATE_PATH)
    xml_ops.blank_meeting_title_slot(doc.body)
    xml_ops.ensure_further_notes_page_break(doc.body)
    return doc


def test_find_calendar_block_locates_the_header_block():
    # The calendar chrome now lives once in the Word header part (see
    # xml_ops module docstring) instead of being cloned into the body once
    # per page -- there's exactly one block, regardless of how many meeting/
    # delegated-tasks pages the body currently has.
    doc = fresh_doc()
    block = xml_ops.find_calendar_block(doc.header)
    assert block.title_row is not None
    assert block.dayno_row is not None


def test_find_calendar_block_unaffected_by_body_content():
    with_page = fresh_doc_with_delegated_page()
    without_page = fresh_doc()
    # Both resolve fine -- the header is independent of whatever's in the body.
    xml_ops.find_calendar_block(with_page.header)
    xml_ops.find_calendar_block(without_page.header)


def test_apply_calendar_block_updates_title_and_days():
    doc = fresh_doc()
    fields = calendar_math.header_fields(datetime.date(2026, 12, 25))
    block = xml_ops.find_calendar_block(doc.header)
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
    title_para, _ = xml_ops.find_meeting_unit_template(doc.body)
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
    title_para, _ = xml_ops.find_meeting_unit_template(body)
    assert xml_ops.meeting_title_text(title_para) == "First meeting"

    before = len(body.findall(".//w:tbl", xml_ops.NS))
    xml_ops.insert_meeting_page(body, "Second meeting")
    after = len(body.findall(".//w:tbl", xml_ops.NS))
    assert after == before + 1  # new notes table only -- no per-page calendar header to clone

    titles = [
        xml_ops._paragraph_text(p)[len(xml_ops.MEETING_TITLE_PREFIX):]
        for p in body.findall("w:p", xml_ops.NS)
        if xml_ops._paragraph_text(p).startswith(xml_ops.MEETING_TITLE_PREFIX)
    ]
    assert titles == ["First meeting", "Second meeting"]


def test_insert_meeting_page_keeps_closing_page_last():
    """Regression test: meetings added after create_agenda's initial setup
    must stay ordered before the closing 'Further notes' page, never after
    it."""
    doc = fresh_doc()
    body = doc.body
    xml_ops.insert_meeting_page(body, "First meeting")
    xml_ops.insert_meeting_page(body, "Second meeting")
    xml_ops.insert_meeting_page(body, "Third meeting")

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

    # A page break (not a stacked calendar header -- that's gone, see the
    # module docstring) directly precedes "Further notes".
    before = children[further_notes_index - 1]
    assert before.tag == xml_ops.qn("w:p")
    assert xml_ops._has_page_break(before)


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
    xml_ops.apply_calendar_block(xml_ops.find_calendar_block(doc.header), fields)
    xml_ops.insert_meeting_page(doc.body, "Roundtrip check")

    out = tmp_path / "roundtrip.docx"
    doc.save(out)

    reloaded = agenda_store.AgendaDocument.load(out)
    title_para, _ = xml_ops.find_meeting_unit_template(reloaded.body)
    assert xml_ops.meeting_title_text(title_para) == "Roundtrip check"


def _task(text, cadence="daily", owner="", marked=False, status=""):
    return {"text": text, "cadence": cadence, "owner": owner, "marked": marked, "status": status}


def test_rebuild_delegated_tasks_noop_when_absent_and_empty():
    doc = fresh_doc()  # delegated page already dropped
    assert xml_ops.find_delegated_tables(doc.body) == []
    xml_ops.rebuild_delegated_tasks(doc.body, [])
    assert xml_ops.find_delegated_tables(doc.body) == []


def test_rebuild_delegated_tasks_creates_page_when_absent():
    doc = fresh_doc()
    assert xml_ops.find_delegated_tables(doc.body) == []
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("Renew the domain")])
    tables = xml_ops.find_delegated_tables(doc.body)
    assert len(tables) == 1
    rows = tables[0].findall("w:tr", xml_ops.NS)
    assert len(rows) == 2  # header + 1 data row


def test_rebuild_delegated_tasks_removes_page_when_emptied():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("Renew the domain")])
    assert xml_ops.find_delegated_tables(doc.body) != []
    xml_ops.rebuild_delegated_tasks(doc.body, [])
    assert xml_ops.find_delegated_tables(doc.body) == []


def test_rebuild_delegated_tasks_leaves_no_dangling_section_boundary():
    """Regression test: the delegated-tasks page lives in its own OOXML
    section (so word/footer1.xml applies only there) -- if its boundary
    paragraph were ever left behind after emptying the list, that section
    would still consume a blank page of its own even with no table in it."""
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("Renew the domain")])
    xml_ops.rebuild_delegated_tasks(doc.body, [])
    with pytest.raises(MagendaError):
        xml_ops._find_delegated_section_boundary(doc.body)


def test_rebuild_delegated_tasks_no_trailing_empty_rows():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("A"), _task("B"), _task("C")])
    table = xml_ops.find_delegated_tables(doc.body)[0]
    assert len(table.findall("w:tr", xml_ops.NS)) == 4  # header + exactly 3


def test_read_delegated_tasks_roundtrips_fields():
    doc = fresh_doc()
    tasks = [
        _task("Ship the report", cadence="weekly", owner="Andrea", marked=True, status="In progress"),
        _task("Water the plants", cadence="daily", owner="Taki"),
    ]
    xml_ops.rebuild_delegated_tasks(doc.body, tasks)
    readback = xml_ops.read_delegated_tasks(doc.body)
    assert len(readback) == 2
    by_text = {t["text"]: t for t in readback}
    assert by_text["Ship the report"]["cadence"] == "weekly"
    assert by_text["Ship the report"]["owner"] == "Andrea"
    assert by_text["Ship the report"]["marked"] is True
    assert by_text["Ship the report"]["status"] == "In progress"
    assert by_text["Water the plants"]["cadence"] == "daily"
    assert by_text["Water the plants"]["marked"] is False


def test_delegated_tasks_rows_are_numbered_in_order():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("A"), _task("B"), _task("C")])
    table = xml_ops.find_delegated_tables(doc.body)[0]
    rows = table.findall("w:tr", xml_ops.NS)[1:]
    numbers = [xml_ops.cell_text(row.findall("w:tc", xml_ops.NS)[0]) for row in rows]
    assert numbers == ["1", "2", "3"]


def test_delegated_row_number_matches_header_color_body_text_is_black():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(
        doc.body, [_task("A", owner="Andrea", status="Doing fine")]
    )
    table = xml_ops.find_delegated_tables(doc.body)[0]
    header_color = table.findall("w:tr", xml_ops.NS)[0].findall("w:tc", xml_ops.NS)[1].find(
        "w:p/w:r/w:rPr/w:color", xml_ops.NS
    ).get(xml_ops.qn("w:val"))
    data_row = table.findall("w:tr", xml_ops.NS)[1]
    cells = data_row.findall("w:tc", xml_ops.NS)
    number_cell, task_cell, owner_cell, status_cell = cells
    for color in number_cell.findall(".//w:color", xml_ops.NS):
        assert color.get(xml_ops.qn("w:val")) == header_color
    for cell in (task_cell, owner_cell, status_cell):
        for color in cell.findall(".//w:color", xml_ops.NS):
            assert color.get(xml_ops.qn("w:val")) == "000000"


def test_delegated_header_row_repeats_across_pages():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("A")])
    table = xml_ops.find_delegated_tables(doc.body)[0]
    header_row = table.findall("w:tr", xml_ops.NS)[0]
    assert header_row.find("w:trPr/w:tblHeader", xml_ops.NS) is not None


def test_delegated_body_font_size_is_one_point_above_old_default():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("A", owner="Andrea")])
    table = xml_ops.find_delegated_tables(doc.body)[0]
    owner_cell = table.findall("w:tr", xml_ops.NS)[1].findall("w:tc", xml_ops.NS)[2]
    sz = owner_cell.find("w:p/w:r/w:rPr/w:sz", xml_ops.NS)
    assert sz.get(xml_ops.qn("w:val")) == "22"  # 11pt, was 10pt


def test_owner_column_centered():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("A", owner="Andrea")])
    table = xml_ops.find_delegated_tables(doc.body)[0]
    owner_cell = table.findall("w:tr", xml_ops.NS)[1].findall("w:tc", xml_ops.NS)[2]
    jc = owner_cell.find("w:p/w:pPr/w:jc", xml_ops.NS)
    assert jc is not None and jc.get(xml_ops.qn("w:val")) == "center"


def test_status_multiline_becomes_one_bullet_per_line_and_roundtrips():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(
        doc.body, [_task("A", status="Draft sent\nWaiting on finance\nDue Friday")]
    )
    table = xml_ops.find_delegated_tables(doc.body)[0]
    status_cell = table.findall("w:tr", xml_ops.NS)[1].findall("w:tc", xml_ops.NS)[3]
    lines = xml_ops._paragraph_lines(status_cell.find("w:p", xml_ops.NS))
    assert lines == [
        f"{xml_ops.DELEGATED_BULLET_PREFIX}Draft sent",
        f"{xml_ops.DELEGATED_BULLET_PREFIX}Waiting on finance",
        f"{xml_ops.DELEGATED_BULLET_PREFIX}Due Friday",
    ]

    readback = xml_ops.read_delegated_tasks(doc.body)
    assert readback[0]["status"] == "Draft sent\nWaiting on finance\nDue Friday"


def test_status_long_entry_wraps_instead_of_truncating_and_roundtrips():
    long_entry = (
        "Waiting on finance approval which is taking longer than expected "
        "due to end of quarter reviews"
    )
    status = f"Draft sent\n{long_entry}\nDue Friday"
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("A", status=status)])
    table = xml_ops.find_delegated_tables(doc.body)[0]
    status_cell = table.findall("w:tr", xml_ops.NS)[1].findall("w:tc", xml_ops.NS)[3]
    lines = xml_ops._paragraph_lines(status_cell.find("w:p", xml_ops.NS))

    # The long middle entry wraps across more than one physical line; only
    # its first physical line carries the bullet, so it counts as one entry
    # rather than several.
    assert lines[0] == f"{xml_ops.DELEGATED_BULLET_PREFIX}Draft sent"
    assert lines[-1] == f"{xml_ops.DELEGATED_BULLET_PREFIX}Due Friday"
    middle = lines[1:-1]
    assert len(middle) > 1
    assert middle[0].startswith(xml_ops.DELEGATED_BULLET_PREFIX)
    assert not any(line.startswith(xml_ops.DELEGATED_BULLET_PREFIX) for line in middle[1:])

    readback = xml_ops.read_delegated_tasks(doc.body)
    assert readback[0]["status"] == status


def test_delegated_tasks_marked_rows_shaded_unmarked_not():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("Marked", marked=True), _task("Plain", marked=False)])
    table = xml_ops.find_delegated_tables(doc.body)[0]
    rows = table.findall("w:tr", xml_ops.NS)[1:]
    marked_row, plain_row = rows
    assert xml_ops._row_marked(marked_row) is True
    assert xml_ops._row_marked(plain_row) is False


def test_rebuild_delegated_tasks_orders_marked_first_then_cadence():
    doc = fresh_doc()
    tasks = [
        _task("unmarked monthly", cadence="monthly"),
        _task("marked weekly", cadence="weekly", marked=True),
        _task("unmarked daily", cadence="daily"),
        _task("marked daily", cadence="daily", marked=True),
        _task("marked monthly", cadence="monthly", marked=True),
        _task("unmarked weekly", cadence="weekly"),
    ]
    xml_ops.rebuild_delegated_tasks(doc.body, sorted(
        tasks, key=lambda t: (0 if t["marked"] else 1, xml_ops.DELEGATED_CADENCE_ORDER[t["cadence"]])
    ))
    readback = xml_ops.read_delegated_tasks(doc.body)
    assert [t["text"] for t in readback] == [
        "marked daily",
        "marked weekly",
        "marked monthly",
        "unmarked daily",
        "unmarked weekly",
        "unmarked monthly",
    ]


def test_rebuild_delegated_tasks_spans_multiple_pages():
    doc = fresh_doc()
    n = xml_ops.DELEGATED_ROWS_PER_PAGE + 3
    xml_ops.rebuild_delegated_tasks(doc.body, [_task(f"task {i}") for i in range(n)])
    tables = xml_ops.find_delegated_tables(doc.body)
    assert len(tables) == 2
    assert len(tables[0].findall("w:tr", xml_ops.NS)) == 1 + xml_ops.DELEGATED_ROWS_PER_PAGE
    assert len(tables[1].findall("w:tr", xml_ops.NS)) == 1 + 3  # no trailing empty rows
    assert xml_ops.read_delegated_tasks(doc.body) and len(xml_ops.read_delegated_tasks(doc.body)) == n


def test_rebuild_delegated_tasks_shrinks_pages_back_down():
    doc = fresh_doc()
    n = xml_ops.DELEGATED_ROWS_PER_PAGE + 3
    xml_ops.rebuild_delegated_tasks(doc.body, [_task(f"task {i}") for i in range(n)])
    assert len(xml_ops.find_delegated_tables(doc.body)) == 2

    xml_ops.rebuild_delegated_tasks(doc.body, [_task("only one")])
    tables = xml_ops.find_delegated_tables(doc.body)
    assert len(tables) == 1
    assert len(tables[0].findall("w:tr", xml_ops.NS)) == 2


def test_no_adjacent_tables_around_delegated_page():
    doc = fresh_doc()
    xml_ops.rebuild_delegated_tasks(doc.body, [_task("A")])
    xml_ops.insert_meeting_page(doc.body, "A meeting")

    children = list(doc.body)
    for prev, cur in zip(children, children[1:]):
        if prev.tag == xml_ops.qn("w:tbl") and cur.tag == xml_ops.qn("w:tbl"):
            pytest.fail("found two adjacent <w:tbl> elements with no separating paragraph")
