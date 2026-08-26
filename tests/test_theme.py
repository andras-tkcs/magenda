import shutil

import pytest

from magenda import agenda_store, config, theme
from magenda.theme import Theme
from magenda.xml_ops import NS, MagendaError, qn


def _rfonts_ascii_values(tree) -> set[str]:
    values = set()
    for rfonts in tree.iter(qn("w:rFonts")):
        val = rfonts.get(qn("w:ascii"))
        if val:
            values.add(val)
    return values


def _color_values(tree) -> set[str]:
    values = set()
    for color_el in tree.iter(qn("w:color")):
        val = color_el.get(qn("w:val"))
        if val:
            values.add(val.upper())
    return values


def _color_values_across(trees) -> set[str]:
    values: set[str] = set()
    for tree in trees:
        values |= _color_values(tree)
    return values


def fresh_doc():
    return agenda_store.AgendaDocument.load(agenda_store.TEMPLATE_PATH)


def fresh_tree():
    return fresh_doc().tree


def test_theme_defaults_match_template():
    t = Theme()
    assert t.font_pack == "outfit"
    assert t.weekend_color == "EE0000"
    assert t.heading_color == "215E99"
    assert t.label_color == "BF4E14"
    assert t.accent_color == "3A7C22"
    assert t.notes_color == "00B0F0"


def test_apply_font_pack_swaps_all_five_outfit_weights():
    tree = fresh_tree()
    before = _rfonts_ascii_values(tree)
    assert "Outfit Black" in before
    assert "Outfit Thin" in before

    theme.apply_font_pack(tree, "roboto")
    after = _rfonts_ascii_values(tree)

    assert not any(name.startswith("Outfit") for name in after)
    assert "Roboto Black" in after
    assert "Roboto Thin" in after
    assert "Roboto" in after


def test_apply_font_pack_leaves_non_outfit_fonts_untouched():
    tree = fresh_tree()
    before = _rfonts_ascii_values(tree)
    assert "Wingdings" in before  # delegated-tasks checkbox glyph

    theme.apply_font_pack(tree, "roboto")
    after = _rfonts_ascii_values(tree)
    assert "Wingdings" in after


def test_apply_font_pack_unknown_pack_raises():
    tree = fresh_tree()
    with pytest.raises(MagendaError):
        theme.apply_font_pack(tree, "comic-sans")


def test_apply_font_pack_size_scale_shrinks_only_swapped_runs():
    tree = fresh_tree()
    sizes_before = {
        el.get(qn("w:val"))
        for el in tree.iter(qn("w:sz"))
    }
    theme.apply_font_pack(tree, "jetbrains_mono")  # size_scale = 0.9
    sizes_after = {el.get(qn("w:val")) for el in tree.iter(qn("w:sz"))}
    assert sizes_after != sizes_before


def test_apply_colors_swaps_the_known_accents_present_in_one_part():
    # document.xml's own body carries 4 of the 5 known accents (weekend,
    # label, accent, notes) -- the heading color lives only in the Word
    # header part (word/header1.xml), which apply_colors doesn't reach on
    # its own; see test_apply_theme_to_document_covers_every_part below for
    # the header/footer parts too.
    tree = fresh_tree()
    before = _color_values(tree)
    assert {"EE0000", "BF4E14", "3A7C22", "00B0F0"} <= before

    custom = Theme(weekend_color="111111", heading_color="222222", label_color="333333", accent_color="444444", notes_color="555555")
    theme.apply_colors(tree, custom)
    after = _color_values(tree)

    assert not ({"EE0000", "BF4E14", "3A7C22", "00B0F0"} & after)
    assert {"111111", "333333", "444444", "555555"} <= after


def test_apply_colors_leaves_unrelated_colors_untouched():
    # Any w:color that isn't one of the 5 known constants must survive
    # unchanged. F95738 is one such leftover: the template's own shipped
    # sample delegated-tasks rows (dropped by create_agenda, never part of
    # a rebuilt page -- see xml_ops._delegated_body_rpr, which colors
    # *generated* rows with label_color instead) bake it into their
    # paragraph-mark formatting, and it isn't one of the 5 themed roles.
    tree = fresh_tree()
    before = _color_values(tree)
    assert "F95738" in before
    theme.apply_colors(tree, Theme())  # defaults == template's own values
    after = _color_values(tree)
    assert before == after


def test_apply_theme_is_font_and_color_together():
    tree = fresh_tree()
    custom = Theme(font_pack="roboto", weekend_color="ABCDEF")
    theme.apply_theme(tree, custom)
    assert "Roboto Black" in _rfonts_ascii_values(tree)
    assert "ABCDEF" in _color_values(tree)


def test_apply_theme_to_document_covers_every_part():
    """The calendar heading color lives only in the header part, and the
    "Notes and updates" footer heading only in footer1.xml -- a themed
    render has to reach all of them, not just document.xml's body."""
    doc = fresh_doc()
    all_before = _color_values_across(doc.themable_trees())
    assert {"EE0000", "215E99", "BF4E14", "3A7C22", "00B0F0"} <= all_before

    custom = Theme(
        weekend_color="111111",
        heading_color="222222",
        label_color="333333",
        accent_color="444444",
        notes_color="555555",
    )
    theme.apply_theme_to_document(doc, custom)
    all_after = _color_values_across(doc.themable_trees())
    assert not ({"EE0000", "215E99", "BF4E14", "3A7C22", "00B0F0"} & all_after)
    assert {"111111", "222222", "333333", "444444", "555555"} <= all_after


# -- config.py -------------------------------------------------------------


def test_valid_pack_falls_back_on_unknown():
    assert config._valid_pack("roboto") == "roboto"
    assert config._valid_pack("not-a-pack") == "outfit"
    assert config._valid_pack(None) == "outfit"


def test_valid_color_falls_back_on_malformed():
    assert config._valid_color("1a2b3c", "000000") == "1A2B3C"
    assert config._valid_color("not-a-color", "000000") == "000000"
    assert config._valid_color("12345", "000000") == "000000"  # too short
    assert config._valid_color(None, "000000") == "000000"


def test_theme_from_env(monkeypatch):
    monkeypatch.setenv("MAGENDA_FONT_PACK", "jetbrains_mono")
    monkeypatch.setenv("MAGENDA_LABEL_COLOR", "abcdef")
    monkeypatch.delenv("MAGENDA_HEADING_COLOR", raising=False)
    t = config._theme_from_env()
    assert t.font_pack == "jetbrains_mono"
    assert t.label_color == "ABCDEF"
    assert t.heading_color == "215E99"  # unset -> template default


def test_theme_from_env_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("MAGENDA_FONT_PACK", "helvetica")
    monkeypatch.setenv("MAGENDA_WEEKEND_COLOR", "not-a-hex-color")
    t = config._theme_from_env()
    assert t.font_pack == "outfit"
    assert t.weekend_color == "EE0000"


# -- end-to-end: the real render_pdf tool applies the active theme --------
# (module-level pytestmark would skip every test above too, not just these
# two -- so it's a per-test decorator here instead)

_needs_soffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")


@_needs_soffice
def test_render_pdf_applies_active_theme(tmp_path, monkeypatch):
    import fitz

    from magenda import tools

    monkeypatch.setattr(config, "get_active_theme", lambda: Theme(font_pack="roboto", label_color="123456"))

    date = "2026-08-14"
    tools.create_agenda(date)
    result = tools.render_pdf(date, output_dir=str(tmp_path))
    doc = fitz.open(result["path"])

    found_label = False
    for block in doc[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip() == "TO-DO LIST":
                    assert "Roboto" in span["font"]
                    assert span["color"] == 0x123456
                    found_label = True
    assert found_label


@_needs_soffice
def test_render_pdf_never_mutates_shared_working_doc(tmp_path, monkeypatch):
    """theme.apply_theme_to_document must run on a clone -- a themed render
    must not leave the in-memory working document (which every later tool
    call reads) with renamed fonts."""
    from magenda import tools

    monkeypatch.setattr(config, "get_active_theme", lambda: Theme(font_pack="roboto"))

    date = "2026-08-15"
    tools.create_agenda(date)
    tools.render_pdf(date, output_dir=str(tmp_path))

    live_doc = agenda_store.load(__import__("datetime").date.fromisoformat(date))
    assert "Outfit Black" in _rfonts_ascii_values(live_doc.tree)
    assert not any(name.startswith("Roboto") for name in _rfonts_ascii_values(live_doc.tree))
