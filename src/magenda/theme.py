"""Render-time visual theming: which font-pack file and which of 5 accent
colors a slot's `role` (see slot_schema.Slot) draws with.

Since every themable run -- static labels included, not just dynamic
content -- is drawn fresh at render time (see pdf_assembler.py and the
design doc's answer to "would there be multiple compiled templates per
font/color": there's exactly one, because nothing themable is ever baked
into assets/compiled/chrome.pdf), theming here is just a lookup: role ->
hex color, weight bucket -> .ttf path. No OOXML rewriting, no subprocess.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from magenda.errors import MagendaError
from magenda.font_packs import FONT_PACKS
from magenda.paths import FONTS_DIR

# The template's own baked-in accent colors (see assets/template.docx),
# keyed by the structural role they play -- see slot_schema.ROLES:
#   weekend  — Saturday/Sunday weekday-header labels and date numbers
#   heading  — the big day/month/year heading ("19 TUESDAY", "MAY 2026")
#   label    — section headers and table column headers (TO-DO LIST, DAILY
#              SCHEDULE, Task & cadence/Owner/Status) and the delegated-tasks
#              row numbers
#   accent   — "Meeting title:" (and its typed title) and the delegated-
#              tasks page's own "Notes and updates" footer heading
#   notes    — the closing "Further notes from today" header
#   body     — everything else (task/due text, schedule notes, delegated
#              task/owner/status body text): always plain black, never
#              themed -- there's no Theme field for it.
_ORIGINAL_COLORS = {
    "weekend_color": "EE0000",
    "heading_color": "215E99",
    "label_color": "BF4E14",
    "accent_color": "3A7C22",
    "notes_color": "00B0F0",
}

_ROLE_TO_FIELD = {
    "weekend": "weekend_color",
    "heading": "heading_color",
    "label": "label_color",
    "accent": "accent_color",
    "notes": "notes_color",
}

BODY_COLOR = "000000"


@dataclass(frozen=True)
class Theme:
    """A font pack + 5 accent colors. Defaults are the template's own
    values, so Theme() round-trips to a no-op when applied."""

    font_pack: str = "outfit"
    weekend_color: str = _ORIGINAL_COLORS["weekend_color"]
    heading_color: str = _ORIGINAL_COLORS["heading_color"]
    label_color: str = _ORIGINAL_COLORS["label_color"]
    accent_color: str = _ORIGINAL_COLORS["accent_color"]
    notes_color: str = _ORIGINAL_COLORS["notes_color"]


def role_color(theme: Theme, role: str) -> str:
    """Hex RRGGBB (no '#') `role` draws with under `theme`. 'body' is
    always plain black, regardless of theme."""
    field = _ROLE_TO_FIELD.get(role)
    return getattr(theme, field) if field else BODY_COLOR


def role_font_file(theme: Theme, weight: str) -> Path:
    """Path to the .ttf file for `weight` (font_packs.WEIGHT_BUCKETS) under
    `theme`'s font pack."""
    if theme.font_pack not in FONT_PACKS:
        raise MagendaError(f"unknown font pack {theme.font_pack!r}; available: {sorted(FONT_PACKS)}")
    pack = FONT_PACKS[theme.font_pack]
    return FONTS_DIR / pack["files"][weight]


def role_size_scale(theme: Theme) -> float:
    """A pack's size_scale (see font_packs.py) -- applied to every
    themable run's font size when drawing under this theme, so a face
    measurably wider than Outfit at matching weight (e.g. a monospace
    pack) doesn't overflow the cell its line breaks were computed for."""
    return FONT_PACKS.get(theme.font_pack, {}).get("size_scale", 1.0)
