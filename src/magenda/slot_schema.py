"""The compiled-template bundle's geometry manifest (assets/compiled/
slots.json): what scripts/compile_template.py writes and
compiled_template.py reads back at runtime. See docs/design/
remove-libreoffice-runtime-dependency.md section 6.

A Slot describes one rectangle of themable text on one of the compiled
bundle's blank "chrome" pages -- everything from a static label ("TO-DO
LIST") to a genuinely dynamic one (a to-do row's task cell). Both are
drawn the same way at render time (pdf_assembler.py): pick the active
theme's .ttf and hex color for the slot's `role`, then insert text into
`rect` -- the slot's own `text` field if it's static, or whatever
AgendaState holds for `id` if it's dynamic. There's no separate "baked
static label" path; see the design doc's answer to "would there be
multiple compiled templates per font/color" -- there's exactly one,
because no themable glyph is ever baked into assets/compiled/chrome.pdf.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

# The 5 roles theme.py's Theme dataclass carries a color for, plus "body"
# for themable-in-font-only, never-recolored plain text (task/due/schedule
# note text, delegated task/owner/status body text -- see README's "Look
# and feel" table: these stay black regardless of theme).
ROLES = ("heading", "label", "accent", "notes", "weekend", "body")

# font_packs.WEIGHT_BUCKETS, repeated here rather than imported so this
# module (read by both runtime and the compiler) has no dependency beyond
# the standard library.
WEIGHTS = ("thin", "extralight", "regular", "semibold", "black")

ALIGN = ("left", "center")


@dataclass(frozen=True)
class Slot:
    id: str
    rect: tuple[float, float, float, float]  # x0, y0, x1, y1 -- PDF points, top-left origin
    role: str
    weight: str
    size_half_points: int
    align: str = "left"
    text: str | None = None  # fixed content for a static slot; None if dynamic (from AgendaState)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Slot":
        return cls(
            id=data["id"],
            rect=tuple(data["rect"]),
            role=data["role"],
            weight=data["weight"],
            size_half_points=data["size_half_points"],
            align=data.get("align", "left"),
            text=data.get("text"),
        )


@dataclass
class DelegatedGeometry:
    """Everything pdf_assembler.py needs to draw delegated-task rows
    procedurally at runtime (see layout_constants.py for the column
    widths/border sizes this positions) -- captured once by the compiler's
    row-height calibration render rather than baked as PDF content, since
    the number of rows per page is fully dynamic (0..DELEGATED_ROWS_PER_PAGE)."""

    table_top_left: tuple[float, float]
    row_overhead_twips: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DelegatedGeometry":
        return cls(
            table_top_left=tuple(data["table_top_left"]),
            row_overhead_twips=data["row_overhead_twips"],
        )


@dataclass
class CompiledManifest:
    template_docx_sha256: str
    page_width: float
    page_height: float
    chrome_pages: dict  # role -> page index in chrome.pdf
    header_slots: list  # list[Slot] -- repeats identically on every physical page
    page_slots: dict  # role -> list[Slot] -- e.g. "overview" -> [...], "meeting_unit" -> [...]
    delegated: DelegatedGeometry

    def to_dict(self) -> dict:
        return {
            "template_docx_sha256": self.template_docx_sha256,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "chrome_pages": self.chrome_pages,
            "header_slots": [s.to_dict() for s in self.header_slots],
            "page_slots": {role: [s.to_dict() for s in slots] for role, slots in self.page_slots.items()},
            "delegated": self.delegated.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CompiledManifest":
        return cls(
            template_docx_sha256=data["template_docx_sha256"],
            page_width=data["page_width"],
            page_height=data["page_height"],
            chrome_pages=data["chrome_pages"],
            header_slots=[Slot.from_dict(s) for s in data["header_slots"]],
            page_slots={role: [Slot.from_dict(s) for s in slots] for role, slots in data["page_slots"].items()},
            delegated=DelegatedGeometry.from_dict(data["delegated"]),
        )
