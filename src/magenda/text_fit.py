"""Text fitting for fixed-width table cells.

`fit_single_line` truncates (never wraps, never ellipsizes) — used for the
daily schedule notes and meeting titles, which are single ruled/aligned
slots where letting Word/LibreOffice wrap long text would break the
template's fixed layout.

`fit_downsize_or_wrap` shrinks the font first and only wraps as a last
resort — used for the to-do list, whose rows are allowed to grow.

`wrap_text` wraps at a fixed font size, no downsizing — used for a
delegated task's status updates, whose row is allowed to grow but whose
font size is fixed regardless of how many lines that takes.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from magenda.font_packs import FONT_PACKS
from magenda.paths import FONTS_DIR

# Flattened from every registered pack (see font_packs.py): family name (as
# it appears in a run's w:rFonts) -> its ttf filename under FONTS_DIR. Covers
# whichever pack a run's font was swapped to by theme.apply_font_pack, as
# well as the template's own original Outfit names.
_FONT_FILES: dict[str, str] = {}
for _pack in FONT_PACKS.values():
    for _bucket, _family in _pack["weights"].items():
        _FONT_FILES[_family] = _pack["files"][_bucket]


@lru_cache(maxsize=None)
def _font(family: str, size_pt: int) -> ImageFont.FreeTypeFont:
    filename = _FONT_FILES.get(family, _FONT_FILES["Outfit"])
    return ImageFont.truetype(str(FONTS_DIR / filename), size_pt)


def _width_pt(text: str, font: ImageFont.FreeTypeFont) -> float:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def text_width_twips(text: str, *, family: str, size_half_points: int) -> float:
    """Rendered width of `text` at the given font/size, in twips."""
    size_pt = max(1, round(size_half_points / 2))
    font = _font(family, size_pt)
    return _width_pt(text, font) * 20


def text_line_height_twips(family: str, size_half_points: int) -> float:
    """Rendered line height (ascent + descent) at the given font/size, in
    twips. Used to figure out how many of the template's fixed-height rows a
    block of wrapped, possibly downsized, lines actually needs — a row sized
    for one line at the default size can often hold more than one line once
    the font has been shrunk."""
    size_pt = max(1, round(size_half_points / 2))
    font = _font(family, size_pt)
    ascent, descent = font.getmetrics()
    return (ascent + descent) * 20


def fit_single_line(text: str, *, family: str, size_half_points: int, max_width_twips: int) -> str:
    """Return `text`, truncated from the end (no ellipsis) so it renders on
    a single line within `max_width_twips` at the given font/size. Returns
    `text` unchanged if it already fits."""
    if not text:
        return text
    size_pt = max(1, round(size_half_points / 2))
    font = _font(family, size_pt)
    max_width_pt = max_width_twips / 20
    if _width_pt(text, font) <= max_width_pt:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _width_pt(text[:mid], font) <= max_width_pt:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _wrap_words(text: str, font: ImageFont.FreeTypeFont, max_width_pt: float) -> list[str]:
    """Word-wrap `text` (never truncate) across as many lines as needed to
    keep every line within `max_width_pt` at `font`'s size."""
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if not current or _width_pt(candidate, font) <= max_width_pt:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def wrap_text(text: str, *, family: str, size_half_points: int, max_width_twips: int) -> list[str]:
    """Word-wrap `text` (never truncate, font size fixed) across as many
    lines as needed to fit `max_width_twips` at the given font/size. Unlike
    `fit_downsize_or_wrap`, this never shrinks the font first -- used where
    the size is already fixed (e.g. a delegated task's status updates) and
    a too-long line should grow the row instead of losing text."""
    if not text:
        return [text]
    font = _font(family, max(1, round(size_half_points / 2)))
    max_width_pt = max_width_twips / 20
    if _width_pt(text, font) <= max_width_pt:
        return [text]
    return _wrap_words(text, font, max_width_pt)


def fit_downsize_or_wrap(
    text: str,
    *,
    family: str,
    max_size_half_points: int,
    min_size_half_points: int,
    max_width_twips: int,
) -> tuple[list[str], int]:
    """Fit `text` into a cell of `max_width_twips`: first try shrinking the
    font in 1pt steps from `max_size_half_points` down to
    `min_size_half_points` looking for a size that fits on one line; if it
    still doesn't fit at the minimum size, keep that size and word-wrap
    (never truncate) across as many lines as needed. Returns (lines,
    size_half_points)."""
    if not text:
        return [text], max_size_half_points

    max_width_pt = max_width_twips / 20
    size = max_size_half_points
    while size > min_size_half_points:
        if _width_pt(text, _font(family, max(1, round(size / 2)))) <= max_width_pt:
            return [text], size
        size -= 2  # 1pt steps (half-points)
    size = min_size_half_points
    font = _font(family, max(1, round(size / 2)))
    if _width_pt(text, font) <= max_width_pt:
        return [text], size

    return _wrap_words(text, font, max_width_pt), size
