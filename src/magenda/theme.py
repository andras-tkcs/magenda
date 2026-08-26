"""Render-time visual theming: swap the template's default Outfit font
family and its 4 accent colors for a certified alternative (see
font_packs.py and Theme below). This module only produces a themed
*rendered PDF*; it never mutates the shared in-memory working document.

Why that matters: agenda_store._STORE holds exactly one AgendaDocument
instance per date for the whole server process, and every tool call
(add_meeting, adjust_dates, ...) both reads and mutates that same instance.
Several structural lookups elsewhere key off the literal Outfit family names
baked into the template (e.g. xml_ops._is_calendar_title_row matches
"Outfit Black") — renaming those in place would silently break every later
call for that date. So apply_theme always runs against an independent clone
(AgendaDocument.from_bytes(doc.to_bytes())), never against the object
agenda_store.load() returns.

This module deliberately doesn't import anything from magenda.tools: that
package's __init__.py eagerly imports tools/render.py, which imports this
module (to apply the active theme before every render) — importing
magenda.tools.anything from here would be circular.
"""
from __future__ import annotations

import datetime
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from magenda import agenda_store, font_setup
from magenda.agenda_store import AgendaDocument
from magenda.font_packs import FONT_PACKS
from magenda.soffice import find_soffice
from magenda.xml_ops import MagendaError, qn

_OUTFIT_WEIGHTS = FONT_PACKS["outfit"]["weights"]

# The template's own baked-in accent colors (see assets/template.docx),
# keyed by the structural role they play there:
#   weekend  — Saturday/Sunday weekday-header labels and date numbers
#   heading  — the big day/month/year heading ("19 TUESDAY", "MAY 2026"),
#              now in the document's Word header part
#   label    — section headers and table column headers (TO-DO LIST, DAILY
#              SCHEDULE, Task & cadence/Owner/Status), the delegated-tasks
#              row numbers, and delegated-tasks body text (task/owner/status)
#   accent   — "Meeting title:" and the delegated-tasks page's own
#              "Notes and updates" footer heading
#   notes    — the closing "Further notes from today" header
_ORIGINAL_COLORS = {
    "weekend_color": "EE0000",
    "heading_color": "215E99",
    "label_color": "BF4E14",
    "accent_color": "3A7C22",
    "notes_color": "00B0F0",
}


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


def _parse_date(date: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(date)
    except ValueError:
        raise MagendaError(f"date must be in YYYY-MM-DD format, got {date!r}") from None


def apply_font_pack(tree: etree._ElementTree, pack_id: str) -> None:
    """Rewrite every w:rFonts reference to one of the template's 5 Outfit
    weight names to `pack_id`'s matching-weight family name, in place on
    `tree`. Leaves everything else untouched — w:cs complex-script
    fallbacks, Wingdings (used for the delegated-tasks checkbox glyph), and
    any family name that isn't one of the 5 known Outfit weights.

    If the pack declares a `size_scale` (see font_packs.py — for a face
    measurably wider than Outfit at matching weight, e.g. a monospace pack),
    every w:sz/w:szCs sharing an rPr with a substituted w:rFonts is scaled by
    that factor too, so a run's rendered width stays close to what it was
    under Outfit rather than overflowing the cell it was fit to. Static
    labels get scaled along with everything else — they already pass with
    margin to spare, so this only makes them safer, not tighter."""
    if pack_id not in FONT_PACKS:
        raise MagendaError(f"unknown font pack {pack_id!r}; available: {sorted(FONT_PACKS)}")
    pack = FONT_PACKS[pack_id]
    target = pack["weights"]
    substitution = {_OUTFIT_WEIGHTS[bucket]: target[bucket] for bucket in _OUTFIT_WEIGHTS}
    size_scale = pack.get("size_scale", 1.0)

    for rpr in tree.iter(qn("w:rPr")):
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            continue
        ascii_name = rfonts.get(qn("w:ascii"))
        if ascii_name not in substitution:
            continue
        for attr in (qn("w:ascii"), qn("w:hAnsi")):
            current = rfonts.get(attr)
            if current in substitution:
                rfonts.set(attr, substitution[current])
        if size_scale != 1.0:
            for tag in (qn("w:sz"), qn("w:szCs")):
                sz = rpr.find(tag)
                if sz is None:
                    continue
                original = int(sz.get(qn("w:val")))
                sz.set(qn("w:val"), str(max(2, round(original * size_scale))))


def apply_colors(tree: etree._ElementTree, theme: Theme) -> None:
    """Rewrite every w:color that matches one of the template's 4 known
    accent hex values to `theme`'s color for that role, in place on `tree`.
    Body text (the majority of runs) has no explicit w:color — it inherits
    default black — and is untouched, matching every other role/hex value
    that isn't one of the 4 known constants."""
    substitution = {
        _ORIGINAL_COLORS[field].upper(): getattr(theme, field).upper()
        for field in _ORIGINAL_COLORS
    }
    for color_el in tree.iter(qn("w:color")):
        val = color_el.get(qn("w:val"))
        if val and val.upper() in substitution:
            color_el.set(qn("w:val"), substitution[val.upper()])


def apply_theme(tree: etree._ElementTree, theme: Theme) -> None:
    """Apply both the font-pack swap and the color substitution for
    `theme`, in place on `tree`."""
    apply_font_pack(tree, theme.font_pack)
    apply_colors(tree, theme)


def apply_theme_to_document(doc: AgendaDocument, theme: Theme) -> None:
    """Apply `theme` across every themable XML part of `doc` — not just
    document.xml's body content, but also the header/footer parts the
    calendar chrome and the delegated-tasks page's "Notes and updates"
    heading now live in (see AgendaDocument.themable_trees)."""
    for tree in doc.themable_trees():
        apply_theme(tree, theme)


def render_pdf_with_theme(date: str, theme: Theme, output_dir: str) -> Path:
    """Render the working docx for `date` to PDF using `theme`, writing the
    PDF into `output_dir` as `<date>-<font_pack>.pdf`. Operates on an
    independent clone of the working document (see module docstring), so
    the shared in-memory copy every other tool call sees is never touched."""
    d = _parse_date(date)
    live_doc = agenda_store.load(d)
    doc = AgendaDocument.from_bytes(live_doc.to_bytes())
    apply_theme_to_document(doc, theme)

    font_setup.ensure_fonts_installed()
    soffice = find_soffice()

    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="magenda-theme-") as tmp:
        tmp_dir = Path(tmp)
        docx_path = tmp_dir / f"{d.isoformat()}-{theme.font_pack}.docx"
        doc.save(docx_path)

        result = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(tmp_dir), str(docx_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        pdf_tmp_path = tmp_dir / f"{d.isoformat()}-{theme.font_pack}.pdf"
        if result.returncode != 0 or not pdf_tmp_path.exists():
            raise MagendaError(
                f"LibreOffice failed to render the themed preview for {d.isoformat()} "
                f"(exit {result.returncode}): {result.stderr or result.stdout}"
            )
        final_path = out_dir / f"{d.isoformat()}-{theme.font_pack}.pdf"
        final_path.write_bytes(pdf_tmp_path.read_bytes())

    return final_path
