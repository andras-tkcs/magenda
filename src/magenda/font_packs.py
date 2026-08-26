"""Registry of font packs available as alternatives to the template's
default Outfit family.

The template references 5 named weights (see assets/template.docx) that
double as structural signals elsewhere in the codebase (e.g.
xml_ops._is_calendar_title_row matches on the literal family name "Outfit
Black") — never on the *swapped* copy theme.apply_font_pack produces, only
on the original template/working document, so that stays safe regardless of
what's registered here. Each pack below maps those same 5 weight buckets to
its own family names, so a pack can be substituted in without changing which
structural role any given weight plays.

A pack is only added here once it has been run through
scripts/certify_font_pack.py and passed — that script checks it isn't
measurably wider than Outfit (so text-fitting decisions already made against
Outfit for dynamic content stay valid) and that every tight static-label
cell in the template still fits at the candidate font's actual rendered
width. "outfit" is the template's own font and always trivially certifies;
it's included here mainly so it round-trips through the same code path as
every other pack.
"""
from __future__ import annotations

WEIGHT_BUCKETS = ("thin", "extralight", "regular", "semibold", "black")

FONT_PACKS: dict[str, dict] = {
    "outfit": {
        "display_name": "Outfit",
        "license": "OFL-1.1",
        "weights": {
            "thin": "Outfit Thin",
            "extralight": "Outfit ExtraLight",
            "regular": "Outfit",
            "semibold": "Outfit SemiBold",
            "black": "Outfit Black",
        },
        "files": {
            "thin": "Outfit-Thin.ttf",
            "extralight": "Outfit-ExtraLight.ttf",
            "regular": "Outfit-Regular.ttf",
            "semibold": "Outfit-SemiBold.ttf",
            "black": "Outfit-Black.ttf",
        },
    },
    "roboto": {
        "display_name": "Roboto",
        "license": "OFL-1.1",
        "weights": {
            "thin": "Roboto Thin",
            "extralight": "Roboto ExtraLight",
            "regular": "Roboto",
            "semibold": "Roboto SemiBold",
            "black": "Roboto Black",
        },
        "files": {
            "thin": "Roboto-Thin.ttf",
            "extralight": "Roboto-ExtraLight.ttf",
            "regular": "Roboto-Regular.ttf",
            "semibold": "Roboto-SemiBold.ttf",
            "black": "Roboto-Black.ttf",
        },
    },
    "jetbrains_mono": {
        "display_name": "JetBrains Mono",
        "license": "OFL-1.1",
        "weights": {
            "thin": "JetBrains Mono Thin",
            "extralight": "JetBrains Mono ExtraLight",
            "regular": "JetBrains Mono",
            "semibold": "JetBrains Mono SemiBold",
            "black": "JetBrains Mono Black",
        },
        "files": {
            "thin": "JetBrainsMono-Thin.ttf",
            "extralight": "JetBrainsMono-ExtraLight.ttf",
            "regular": "JetBrainsMono-Regular.ttf",
            "semibold": "JetBrainsMono-SemiBold.ttf",
            "black": "JetBrainsMono-Black.ttf",
        },
        # JetBrains Mono's variable wght axis tops out at 800 (Outfit's
        # "black" bucket is a true 900) — the black weight is instantiated
        # at the axis max, one step lighter than the other packs.
        "notes": "black weight clamped to wght=800 (font's axis max, not 900); monospace",
        # Monospace + a wider average glyph than Outfit at matching weight
        # (measured 103-106% at thin/extralight/regular — see
        # scripts/certify_font_pack.py) means text sized for Outfit can
        # overflow once swapped in: a first full-agenda render at 1.0 scale
        # overflowed the delegated-tasks page by 2 pages. theme.py shrinks
        # every run this pack touches by this factor to compensate.
        "size_scale": 0.9,
    },
}
