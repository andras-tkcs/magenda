"""Regenerate assets/fonts/*.ttf static instances from each pack's canonical
variable-font source under scripts/font_source/.

Why generate instead of downloading static files: none of these families
reliably ship static per-weight TTFs whose family/style name tables exactly
match what the docx template's runs would reference after a font-pack swap
(e.g. ascii="Roboto Black") — off-the-shelf static mirrors don't get this
right, and some packs (Outfit) don't publish static weights at all. We
instantiate the weights we need ourselves and patch the name tables so each
resolves under its own family name, exactly as font_packs.py declares.

Sources (checked into scripts/font_source/, OFL-1.1 licensed, all pulled
from https://github.com/google/fonts):
    Outfit-Variable.ttf       ofl/outfit          (wght 100-900)
    Roboto-Variable.ttf       ofl/roboto          (wght 100-900, wdth pinned)
    JetBrainsMono-Variable.ttf ofl/jetbrainsmono  (wght 100-800)

JetBrains Mono's wght axis tops out at 800, one step lighter than the other
packs' "black" (900) — see the note on its entry in font_packs.py.

Run: python scripts/build_fonts.py [pack_id ...]   (default: all packs)
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "scripts" / "font_source"
OUT_DIR = REPO_ROOT / "assets" / "fonts"

# pack_id -> (source variable font, {style suffix: wght value})
# Style suffix matches font_packs.py's WEIGHT_BUCKETS capitalized, and
# becomes both the output filename suffix and (except "Regular") the family
# name suffix, e.g. wght=900 under "Outfit" -> family "Outfit Black".
PACKS: dict[str, tuple[str, dict[str, int]]] = {
    "outfit": (
        "Outfit-Variable.ttf",
        {"Thin": 100, "ExtraLight": 200, "Regular": 400, "SemiBold": 600, "Black": 900},
    ),
    "roboto": (
        "Roboto-Variable.ttf",
        {"Thin": 100, "ExtraLight": 200, "Regular": 400, "SemiBold": 600, "Black": 900},
    ),
    "jetbrains_mono": (
        "JetBrainsMono-Variable.ttf",
        {"Thin": 100, "ExtraLight": 200, "Regular": 400, "SemiBold": 600, "Black": 800},
    ),
}

# family "prefix" used in output filenames/family names per pack (no spaces
# in filenames, matching the existing Outfit-*.ttf convention).
FAMILY_PREFIX = {
    "outfit": "Outfit",
    "roboto": "Roboto",
    "jetbrains_mono": "JetBrainsMono",
}
FAMILY_DISPLAY = {
    "outfit": "Outfit",
    "roboto": "Roboto",
    "jetbrains_mono": "JetBrains Mono",
}


def build_pack(pack_id: str) -> None:
    source_name, weights = PACKS[pack_id]
    source = SOURCE_DIR / source_name
    if not source.exists():
        raise SystemExit(f"missing font source {source} (see this script's docstring)")
    file_prefix = FAMILY_PREFIX[pack_id]
    display_name = FAMILY_DISPLAY[pack_id]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for style, wght in weights.items():
        font = TTFont(str(source))
        instance = instantiateVariableFont(font, {"wght": wght}, inplace=False)
        family = display_name if style == "Regular" else f"{display_name} {style}"
        name_table = instance["name"]
        for name_id in (1, 16):
            name_table.setName(family, name_id, 3, 1, 0x409)
            name_table.setName(family, name_id, 1, 0, 0)
        for name_id in (2, 17):
            name_table.setName("Regular", name_id, 3, 1, 0x409)
            name_table.setName("Regular", name_id, 1, 0, 0)
        name_table.setName(family, 4, 3, 1, 0x409)
        name_table.setName(family, 4, 1, 0, 0)
        postscript_name = family.replace(" ", "")
        name_table.setName(postscript_name, 6, 3, 1, 0x409)
        name_table.setName(postscript_name, 6, 1, 0, 0)

        out_path = OUT_DIR / f"{file_prefix}-{style}.ttf"
        instance.save(str(out_path))
        print(f"wrote {out_path}  (family={family!r}, wght={wght})")


def main() -> None:
    pack_ids = sys.argv[1:] or list(PACKS)
    for pack_id in pack_ids:
        if pack_id not in PACKS:
            raise SystemExit(f"unknown pack {pack_id!r}; available: {sorted(PACKS)}")
        build_pack(pack_id)


if __name__ == "__main__":
    main()
