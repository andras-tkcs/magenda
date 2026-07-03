"""Manual end-to-end smoke test: builds a realistic agenda for *today* and
renders it to PDF for visual inspection. Not part of the automated pytest
suite — run it directly and eyeball the result:

    python scripts/manual_test.py

Re-running is safe: any existing agenda for today is wiped first, so the
script always starts from a clean template.
"""
from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from magenda import agenda_store, tools  # noqa: E402


def main() -> None:
    today = datetime.date.today()
    date = today.isoformat()
    print(f"=== Magenda manual test — {date} ===\n")

    # Start clean so the script is safe to re-run on the same day.
    for path in (agenda_store.docx_path(today), agenda_store.pdf_path(today)):
        if path.exists():
            path.unlink()
            print(f"removed existing {path}")

    print("\n→ create_agenda")
    print(" ", tools.create_agenda(date))

    long_title = (
        "It is a test meeting with an extreme super long title to check whether it breaks"
    )

    print("\n→ add_meeting x3")
    print(" ", tools.add_meeting(date, "Meeting with the pink pony"))
    print(" ", tools.add_meeting(date, "Meeting with the green ogre"))
    print(" ", tools.add_meeting(date, long_title))
    print("   expected: title cut off at the end, page stays a single page")

    print("\n→ add_daily_schedule")
    schedule_entries = [
        {"time": "09:00", "text": "Start the day"},
        {"time": "10:30", "text": "Meeting with the pink pony"},
        {"time": "11:30", "text": long_title},
        {"time": "14:00", "text": "Meeting with the green ogre"},
        {"time": "16:00", "text": "Close the day"},
    ]
    print(" ", tools.add_daily_schedule(date, schedule_entries))
    print("   expected: the 11:30 entry is cut off at the end, not wrapped")

    print("\n→ add_tasks")
    task_entries = [
        {"text": "Prepare for the meetings today"},
        {"text": "Commit magenda"},
        {"text": "Be kind today"},
        {
            "text": (
                "It is a very long task which describes a whoée story from A to Z "
                "to check whether it is visible"
            )
        },
    ]
    print(" ", tools.add_tasks(date, task_entries))
    print("   expected: the long task shrinks to 9pt, then wraps across multiple lines")

    print("\n→ render_pdf")
    result = tools.render_pdf(date)
    print(" ", result)

    pdf_path = result["path"]

    try:
        import fitz

        page_count = len(fitz.open(pdf_path))
        print(f"\n→ page count: {page_count}")
        print("   expected: 5 — page 1 (to-do + daily schedule) + 3 meeting pages")
        print("   + 1 closing page. No trailing blank page, no page-1 overflow.")
    except ImportError:
        pass

    print(f"\n=== Done. Inspect: {pdf_path} ===")

    if sys.platform == "darwin":
        try:
            subprocess.run(["open", pdf_path], check=False)
        except OSError:
            pass


if __name__ == "__main__":
    main()
