import pytest

from magenda import config, theme
from magenda.theme import Theme


def test_theme_defaults_match_template():
    t = Theme()
    assert t.font_pack == "outfit"
    assert t.weekend_color == "EE0000"
    assert t.heading_color == "215E99"
    assert t.label_color == "BF4E14"
    assert t.accent_color == "3A7C22"
    assert t.notes_color == "00B0F0"


def test_role_color_maps_each_role_to_its_theme_field():
    t = Theme(weekend_color="111111", heading_color="222222", label_color="333333",
              accent_color="444444", notes_color="555555")
    assert theme.role_color(t, "weekend") == "111111"
    assert theme.role_color(t, "heading") == "222222"
    assert theme.role_color(t, "label") == "333333"
    assert theme.role_color(t, "accent") == "444444"
    assert theme.role_color(t, "notes") == "555555"


def test_role_color_body_is_always_black_regardless_of_theme():
    t = Theme(weekend_color="111111", heading_color="222222", label_color="333333",
              accent_color="444444", notes_color="555555")
    assert theme.role_color(t, "body") == theme.BODY_COLOR == "000000"


def test_role_font_file_resolves_every_weight_bucket():
    t = Theme(font_pack="roboto")
    for weight in ("thin", "extralight", "regular", "semibold", "black"):
        path = theme.role_font_file(t, weight)
        assert path.exists()
        assert "Roboto" in path.name


def test_role_font_file_unknown_pack_raises():
    from magenda.errors import MagendaError

    t = Theme(font_pack="comic-sans")
    with pytest.raises(MagendaError):
        theme.role_font_file(t, "regular")


def test_role_size_scale_default_pack_is_a_no_op():
    assert theme.role_size_scale(Theme()) == 1.0


def test_role_size_scale_jetbrains_mono_shrinks():
    assert theme.role_size_scale(Theme(font_pack="jetbrains_mono")) == pytest.approx(0.9)


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


# -- end-to-end: render_pdf applies the active theme, pure Python ----------
# (no LibreOffice needed any more -- pdf_assembler.py is the whole render
# path, so unlike before the rewrite these tests need no skip marker.)


def test_render_pdf_applies_active_theme(monkeypatch):
    import pymupdf

    from magenda import tools

    monkeypatch.setattr(config, "get_active_theme", lambda: Theme(font_pack="roboto", label_color="123456"))

    date = "2026-08-14"
    tools.create_agenda(date)
    result = tools.render_pdf(date)
    import base64
    doc = pymupdf.open(stream=base64.b64decode(result["pdf_base64"]), filetype="pdf")

    found_label = False
    for block in doc[0].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip() == "TO-DO LIST":
                    assert "Roboto" in span["font"]
                    assert span["color"] == 0x123456
                    found_label = True
    assert found_label


def test_render_pdf_never_mutates_shared_working_state(monkeypatch):
    """Theming is resolved purely at draw time from the active Theme --
    there's no in-memory working document for it to mutate any more, but
    the same guarantee (a themed render doesn't change what later tool
    calls for this date see) still needs to hold: re-rendering under a
    different theme must reproduce identical text content."""
    from magenda import agenda_store, tools

    monkeypatch.setattr(config, "get_active_theme", lambda: Theme(font_pack="roboto"))

    date = "2026-08-15"
    tools.create_agenda(date)
    tools.render_pdf(date)

    import datetime
    state_before = agenda_store.load(datetime.date.fromisoformat(date))
    assert state_before.meetings == [""]

    monkeypatch.setattr(config, "get_active_theme", lambda: Theme())
    tools.render_pdf(date)
    state_after = agenda_store.load(datetime.date.fromisoformat(date))
    assert state_after.meetings == [""]
