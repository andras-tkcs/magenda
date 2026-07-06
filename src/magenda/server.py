"""Magenda MCP server: deterministic daily-agenda PDF generation.

Every tool here is a thin wrapper around magenda.tools.* — plain Python
functions that edit a fixed docx template via XML node lookup/splice, never
via free-form generation. The only thing an LLM ever supplies is the data
(dates, task text, meeting titles); layout and formatting always come from
the template.
"""
from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from magenda import tools
from magenda.xml_ops import MagendaError

mcp = FastMCP("magenda")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
def create_agenda(
    date: Annotated[str, Field(description="ISO date YYYY-MM-DD for the new agenda")],
    meetings: Annotated[
        list[str] | None,
        Field(description="Titles for every meeting to add, in order — one meeting page each."),
    ] = None,
    daily_schedule: Annotated[
        list[dict] | None,
        Field(
            description=(
                "List of {time, text} to fill in the page-1 daily schedule. time is "
                "24-hour HH:MM (e.g. '10:30'), must fall between 08:00 and 18:59."
            )
        ),
    ] = None,
    tasks: Annotated[
        list[dict] | None,
        Field(description="List of {text, due} to append to the page-1 to-do list."),
    ] = None,
    render: Annotated[
        bool, Field(description="Render the finished agenda to PDF at the end of this call.")
    ] = False,
    include_base64: Annotated[
        bool,
        Field(description="When rendering, also return the PDF bytes as base64 in the response."),
    ] = False,
    output_dir: Annotated[
        str | None,
        Field(
            description=(
                "When rendering, write the PDF into this directory instead of the "
                "default agenda store (created if it doesn't exist). Ignored unless "
                "`render` is true."
            )
        ),
    ] = None,
) -> dict:
    """Create a new daily agenda for `date` from the fixed template, and
    optionally build it out completely in this single call: populates the
    calendar header (day/weekday/CW/month/year) on every page and the 'NEXT
    FOUR WEEKS' grid, refreshes every calendar block (as adjust_dates would),
    adds every meeting in `meetings`, fills `daily_schedule` slots, appends
    `tasks`, and renders to PDF if `render` is true (to `output_dir` if given).
    Always starts from a blank template — if an agenda for this date already
    exists, it is discarded and replaced. Use adjust_dates/add_meeting/
    add_daily_schedule/add_tasks/render_pdf on their own afterwards for
    one-off adjustments."""
    return tools.create_agenda(
        date,
        meetings=meetings,
        daily_schedule=daily_schedule,
        tasks=tasks,
        render=render,
        include_base64=include_base64,
        output_dir=output_dir,
    )


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def adjust_dates(
    date: Annotated[str, Field(description="ISO date YYYY-MM-DD of the agenda to refresh")],
) -> dict:
    """Regenerate every calendar header/footer block (top of every page, and
    the footer calendar embedded on each meeting page) and the 'NEXT FOUR
    WEEKS' grid for an agenda that already exists on disk."""
    return tools.adjust_dates(date)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def add_meeting(
    date: Annotated[str, Field(description="ISO date YYYY-MM-DD of the agenda to add a meeting to")],
    title: Annotated[str, Field(description="Meeting title, e.g. 'Andrea - 1:1'")],
) -> dict:
    """Add a meeting page: fills the first blank meeting slot, or clones a
    new meeting page (calendar header + title + ruled notes table) and
    appends it before the closing 'Further notes' page. Always renders as a
    single page. A title too long to fit on one line is cut off at the end,
    never wrapped."""
    return tools.add_meeting(date, title)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def add_daily_schedule(
    date: Annotated[str, Field(description="ISO date YYYY-MM-DD of the agenda to edit")],
    entries: Annotated[
        list[dict],
        Field(
            description=(
                "List of {time, text}. time is 24-hour HH:MM (e.g. '10:30'), "
                "must fall between 08:00 and 18:59. Each entry lands on exactly "
                "one of the schedule's two rows per hour (00-29 min -> first row, "
                "30-59 min -> second row) — it never spans multiple rows. Text "
                "that doesn't fit on one line is truncated, never wrapped."
            )
        ),
    ],
) -> dict:
    """Fill specific time slots in the page-1 daily schedule (right column).
    Slots not mentioned are left untouched; call again to fill more."""
    return tools.add_daily_schedule(date, entries)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
def add_tasks(
    date: Annotated[str, Field(description="ISO date YYYY-MM-DD of the agenda to edit")],
    tasks: Annotated[
        list[dict],
        Field(
            description=(
                "List of {text, due}. due is free-form text, e.g. '07/05'. "
                "Long task text shrinks down to 9pt, then wraps across "
                "multiple lines rather than being truncated."
            )
        ),
    ],
) -> dict:
    """Append tasks to the page-1 to-do list (left column), filling the
    first empty rows top-down. Errors if there isn't enough free capacity
    (18 rows total)."""
    return tools.add_tasks(date, tasks)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def render_pdf(
    date: Annotated[str, Field(description="ISO date YYYY-MM-DD of the agenda to render")],
    include_base64: Annotated[
        bool, Field(description="Also return the PDF bytes as base64 in the response")
    ] = False,
    output_dir: Annotated[
        str | None,
        Field(description="Write the PDF into this directory instead of the default agenda store (created if it doesn't exist)."),
    ] = None,
) -> dict:
    """Render the agenda for `date` to PDF via headless LibreOffice, using
    the bundled Outfit fonts so the output is pixel-identical regardless of
    which machine renders it. Pass `output_dir` to control where the PDF
    lands."""
    return tools.render_pdf(date, include_base64=include_base64, output_dir=output_dir)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
