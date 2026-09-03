import pymupdf

from magenda import compiled_template, config, pdf_assembler


def test_draw_text_retries_and_grows_box_when_insert_textbox_first_reports_no_fit(monkeypatch):
    """Regression: _draw_text's own PIL-based box-size estimate (the
    vertical `elif slack < 0` grow, the horizontal `widen`) is only an
    estimate of what pymupdf's real text-layout engine needs -- close,
    per their own comments, but not guaranteed exact, and the two can
    disagree by more on some platform/pymupdf-version combination than
    whatever margin either estimate built in. insert_textbox drops text
    it can't fit rather than clipping it, so a single wrong guess used to
    lose the text outright. _draw_text must keep growing the box and
    retrying -- trusting insert_textbox's own return code over its own
    estimate -- until the text actually fits, however many attempts that
    takes (bounded), rather than a single guess that's merely usually
    right."""
    manifest = compiled_template.load().manifest
    by_id = {s.id: s for s in manifest.page_slots["overview"]}
    slot = by_id["todo.row.0.task"]
    theme = config.get_active_theme()

    doc = pymupdf.open()
    page = doc.new_page(width=manifest.page_width, height=manifest.page_height)

    real_insert_textbox = pymupdf.Page.insert_textbox
    calls = []

    def flaky_insert_textbox(self, rect, text, **kwargs):
        calls.append(pymupdf.Rect(rect))
        if len(calls) <= 2:
            return -5.0  # pretend the real layout engine needed more room, twice
        return real_insert_textbox(self, rect, text, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "insert_textbox", flaky_insert_textbox)

    rect = pdf_assembler._padded(slot.rect)
    pdf_assembler._draw_text(page, rect, "line one\nline two", role=slot.role, weight=slot.weight,
                              size_half_points=18, align="left", theme=theme)

    assert len(calls) == 3  # 2 reported failures + the successful retry
    # Each retry grows the box on both axes rather than repeating the same guess.
    assert calls[1].height > calls[0].height
    assert calls[1].width > calls[0].width
    assert calls[2].height > calls[1].height

    full_text = page.get_text()
    assert "line one" in full_text
    assert "line two" in full_text
