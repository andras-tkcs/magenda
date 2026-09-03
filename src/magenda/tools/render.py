import base64
from pathlib import Path

from magenda import agenda_store, config, pdf_assembler
from magenda.tools._common import parse_date


def render_pdf(date: str, include_base64: bool = False, output_dir: str | None = None) -> dict:
    """Render the working agenda for `date` to PDF, applying the active
    font-pack/color theme (see magenda.config, set via the extension's
    Settings page or MAGENDA_* env vars). Pure Python -- no subprocess, no
    LibreOffice, no OS-level font install: pdf_assembler.assemble() clones
    the compiled template's page shells (assets/compiled/, see
    scripts/compile_template.py) and inserts every slot's text with the
    active theme's font file handed directly to pymupdf. By default
    nothing is left on disk -- the PDF bytes come back as base64. Pass
    `output_dir` to also write a persistent copy there instead (the
    directory is created if it doesn't exist).

    Before returning, internal navigation links are added: each page-1
    daily-schedule entry that names a meeting links to that meeting's notes
    page, the header's "<< Overview" label links back to page 1 on every
    other page, and the header's "Notes >>" label links to the closing
    "Further notes" page (always the last page) on every other page (see
    pdf_links.add_navigation_links)."""
    d = parse_date(date)
    state = agenda_store.load(d)
    pdf_bytes = pdf_assembler.assemble(state, config.get_active_theme())

    response: dict = {"date": d.isoformat()}
    if output_dir:
        out_dir = Path(output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        final_path = out_dir / f"{d.isoformat()}.pdf"
        final_path.write_bytes(pdf_bytes)
        response["path"] = str(final_path)
    if include_base64 or not output_dir:
        response["pdf_base64"] = base64.b64encode(pdf_bytes).decode("ascii")
    return response
