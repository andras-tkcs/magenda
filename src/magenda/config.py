"""Resolves the active Theme (font pack + accent colors) from environment
variables. For the packaged .mcpb extension these are set by Claude
Desktop from its Settings -> Extensions page, per the `user_config` block in
manifest.json; for a from-source or Claude Code install, the same env vars
can be set by hand in the server's registration (see README).

Read once at import time -- there is no live-reload. A Settings change (or
an edited env config) only takes effect once Claude Desktop restarts/
reconnects the server process.
"""
from __future__ import annotations

import os
import re

from magenda.font_packs import FONT_PACKS
from magenda.theme import Theme

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

_DEFAULTS = Theme()


def _valid_pack(value: str | None) -> str:
    if value and value in FONT_PACKS:
        return value
    return _DEFAULTS.font_pack


def _valid_color(value: str | None, default: str) -> str:
    if value and _HEX_RE.match(value):
        return value.upper()
    return default


def _theme_from_env() -> Theme:
    return Theme(
        font_pack=_valid_pack(os.environ.get("MAGENDA_FONT_PACK")),
        weekend_color=_valid_color(os.environ.get("MAGENDA_WEEKEND_COLOR"), _DEFAULTS.weekend_color),
        heading_color=_valid_color(os.environ.get("MAGENDA_HEADING_COLOR"), _DEFAULTS.heading_color),
        label_color=_valid_color(os.environ.get("MAGENDA_LABEL_COLOR"), _DEFAULTS.label_color),
        accent_color=_valid_color(os.environ.get("MAGENDA_ACCENT_COLOR"), _DEFAULTS.accent_color),
        notes_color=_valid_color(os.environ.get("MAGENDA_NOTES_COLOR"), _DEFAULTS.notes_color),
    )


_ACTIVE_THEME = _theme_from_env()


def get_active_theme() -> Theme:
    """The Theme resolved from MAGENDA_* env vars at process startup. Any
    missing or invalid field (unknown pack id, malformed hex color) silently
    falls back to that field's template-default value rather than erroring
    -- a render should never fail because of a bad theme setting."""
    return _ACTIVE_THEME
