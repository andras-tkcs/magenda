from magenda import agenda_store
from magenda.tools._common import parse_date
from magenda.tools.add_daily_schedule import add_daily_schedule
from magenda.tools.add_delegated_tasks import add_delegated_tasks
from magenda.tools.add_meeting import add_meeting
from magenda.tools.add_tasks import add_tasks
from magenda.tools.render import render_pdf


def create_agenda(
    date: str,
    meetings: list[str] | None = None,
    daily_schedule: list[dict] | None = None,
    tasks: list[dict] | None = None,
    delegated_tasks: list[dict] | None = None,
    render: bool = False,
    include_base64: bool = False,
    output_dir: str | None = None,
) -> dict:
    """Create a fresh agenda for `date` from scratch -- one blank meeting
    slot, no delegated-tasks page, calendar fields resolved live from
    `date` at render time. Always starts from a blank template -- if an
    agenda already exists for `date`, it is discarded and replaced.

    Optionally runs the rest of the setup in the same call: adds every
    title in `meetings` (in order, one meeting page each), fills
    `daily_schedule` slots, appends `tasks`, populates the delegated-tasks
    page(s) with `delegated_tasks`, and renders to PDF if `render` is true.
    Each step is skipped if its argument is omitted, and the outcome of
    every step that ran is included in the returned dict."""
    d = parse_date(date)
    agenda_store.create(d)
    result: dict = {"date": d.isoformat()}

    if meetings:
        result["meetings"] = [add_meeting(date, title) for title in meetings]

    if daily_schedule:
        result["daily_schedule"] = add_daily_schedule(date, daily_schedule)

    if tasks:
        result["tasks"] = add_tasks(date, tasks)

    if delegated_tasks:
        result["delegated_tasks"] = add_delegated_tasks(date, delegated_tasks)

    if render:
        result["render"] = render_pdf(date, include_base64=include_base64, output_dir=output_dir)

    return result
