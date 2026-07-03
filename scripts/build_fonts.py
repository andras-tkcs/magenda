"""Regenerate assets/fonts/Outfit-*.ttf static instances from the canonical
Outfit variable font (scripts/font_source/Outfit-Variable.ttf, sourced from
https://github.com/google/fonts/blob/main/ofl/outfit).

Why generate instead of downloading static files: Outfit ships on Google
Fonts only as a variable font; static per-weight TTFs aren't published. We
instantiate the weights we need ourselves so the family/style name tables
exactly match what the docx template's runs reference (e.g. ascii="Outfit
Black"), which off-the-shelf static mirrors don't reliably get right.

Run: python scripts/build_fonts.py
"""
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "scripts" / "font_source" / "Outfit-Variable.ttf"
OUT_DIR = REPO_ROOT / "assets" / "fonts"

WEIGHTS = {
    "Thin": 100,
    "ExtraLight": 200,
    "Regular": 400,
    "SemiBold": 600,
    "Black": 900,
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for style, wght in WEIGHTS.items():
        font = TTFont(SOURCE)
        instance = instantiateVariableFont(font, {"wght": wght}, inplace=False)
        family = "Outfit" if style == "Regular" else f"Outfit {style}"
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

        out_path = OUT_DIR / f"Outfit-{style}.ttf"
        instance.save(out_path)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
