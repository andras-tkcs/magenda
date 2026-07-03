from magenda import agenda_store, calendar_math, xml_ops
from magenda.tools._common import parse_date
from magenda.tools.add_daily_schedule import add_daily_schedule
from magenda.tools.add_meeting import add_meeting
from magenda.tools.add_tasks import add_tasks
from magenda.tools.adjust_dates import adjust_dates
from magenda.tools.render import render_pdf


def create_agenda(
    date: str,
    meetings: list[str] | None = None,
    daily_schedule: list[dict] | None = None,
    tasks: list[dict] | None = None,
    render: bool = False,
    include_base64: bool = False,
    output_dir: str | None = None,
) -> dict:
    """Create a fresh agenda for `date` from the template, with all calendar
    fields (header dates, next-4-weeks grid) already populated for that date.

    Optionally runs the rest of the setup in the same call: refreshes every
    calendar block (as adjust_dates would), adds every title in `meetings`
    (in order, one meeting page each), fills `daily_schedule` slots, appends
    `tasks`, and — if `render` is true — renders the result to PDF (written to
    `output_dir` if given, else the default agenda store). Each step is
    skipped if its argument is omitted, and the outcome of every step that ran
    is included in the returned dict."""
    d = parse_date(date)
    doc = agenda_store.create(d)
    body = doc.body

    fields = calendar_math.header_fields(d)
    for block in xml_ops.find_calendar_blocks(body):
        xml_ops.apply_calendar_block(block, fields)

    n4w_table = xml_ops.find_next_four_weeks_table(body)
    xml_ops.apply_next_four_weeks(n4w_table, calendar_math.next_four_weeks(d))

    path = agenda_store.save(d, doc)
    result: dict = {"date": d.isoformat(), "path": str(path)}

    result["adjust_dates"] = adjust_dates(date)

    if meetings:
        result["meetings"] = [add_meeting(date, title) for title in meetings]

    if daily_schedule:
        result["daily_schedule"] = add_daily_schedule(date, daily_schedule)

    if tasks:
        result["tasks"] = add_tasks(date, tasks)

    if render:
        result["render"] = render_pdf(date, include_base64=include_base64, output_dir=output_dir)

    return result
