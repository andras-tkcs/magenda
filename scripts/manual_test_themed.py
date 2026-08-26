"""Manual smoke test for font-pack theming: builds the same realistic
agenda as manual_test.py once, then renders it through theme.py under every
pack in font_packs.py (plus the untouched Outfit baseline) so the results
can be compared side by side. Not part of the automated pytest suite — run
it directly and eyeball the output:

    python scripts/manual_test_themed.py
"""
from __future__ import annotations

import datetime
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from magenda import theme, tools  # noqa: E402
from magenda.theme import Theme  # noqa: E402


def main() -> None:
    today = datetime.date.today()
    date = today.isoformat()
    print(f"=== Magenda themed manual test — {date} ===\n")

    tools.create_agenda(date)

    long_title = (
        "It is a test meeting with an extreme super long title to check whether it breaks"
    )
    tools.add_meeting(date, "Meeting with the pink pony")
    tools.add_meeting(date, "Meeting with the green ogre")
    tools.add_meeting(date, long_title)

    tools.add_daily_schedule(
        date,
        [
            {"time": "09:00", "text": "Start the day"},
            {"time": "10:30", "text": "Meeting with the pink pony"},
            {"time": "11:30", "text": long_title},
            {"time": "14:00", "text": "Meeting with the green ogre"},
            {"time": "16:00", "text": "Close the day"},
        ],
    )

    tools.add_tasks(
        date,
        [
            {"text": "Prepare for the meetings today"},
            {"text": "Commit magenda"},
            {"text": "Be kind today"},
            {
                "text": (
                    "It is a very long task which describes a whole story from A to Z "
                    "to check whether it is visible"
                )
            },
        ],
    )

    tools.add_delegated_tasks(
        date,
        [
            {"text": "Renew the SSL certs", "owner": "Bence", "cadence": "monthly"},
            {"text": "Ship the weekly report", "owner": "Andrea", "cadence": "weekly", "marked": True},
            {"text": "Water the office plants", "owner": "Taki", "cadence": "daily"},
            {"text": "Back up the shared drive", "owner": "Kata", "cadence": "daily", "marked": True, "status": "In progress"},
        ],
    )

    out_dir = Path(tempfile.gettempdir()) / "magenda-manual-test-themed"

    print("→ render_pdf (Outfit baseline, untouched)")
    baseline = tools.render_pdf(date, output_dir=str(out_dir))
    print(" ", baseline)

    for pack_id in ("roboto", "jetbrains_mono"):
        print(f"\n→ theme.render_pdf_with_theme ({pack_id})")
        path = theme.render_pdf_with_theme(date, Theme(font_pack=pack_id), str(out_dir))
        print(" ", path)

    print(f"\n=== Done. Inspect PDFs under: {out_dir} ===")


if __name__ == "__main__":
    main()
