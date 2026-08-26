import base64
import subprocess
import tempfile
from pathlib import Path

from magenda import agenda_store, config, font_setup, theme
from magenda.agenda_store import AgendaDocument
from magenda.soffice import find_soffice
from magenda.tools._common import parse_date
from magenda.xml_ops import MagendaError

# Kept as an alias: theme.py pre-dates this move and other code may still
# import _find_soffice from here.
_find_soffice = find_soffice


def render_pdf(date: str, include_base64: bool = False, output_dir: str | None = None) -> dict:
    """Render the working docx for `date` to PDF via headless LibreOffice,
    after ensuring the bundled fonts are installed so the output is
    pixel-identical regardless of which machine renders it. Applies the
    active font-pack/color theme (see magenda.config, set via the extension's
    Settings page or MAGENDA_* env vars) to a throwaway clone of the working
    document -- the in-memory working agenda itself is never touched, so
    later tool calls for this date keep seeing the template's original
    Outfit-named runs regardless of what's configured. The conversion itself
    happens in a throwaway temp directory that's removed as soon as this
    call returns. By default nothing is left on disk -- the PDF bytes come
    back as base64. Pass `output_dir` to also write a persistent copy there
    instead (the directory is created if it doesn't exist)."""
    d = parse_date(date)
    live_doc = agenda_store.load(d)
    doc = AgendaDocument.from_bytes(live_doc.to_bytes())
    theme.apply_theme_to_document(doc, config.get_active_theme())

    font_setup.ensure_fonts_installed()
    soffice = find_soffice()

    with tempfile.TemporaryDirectory(prefix="magenda-") as tmp:
        tmp_dir = Path(tmp)
        docx_path = tmp_dir / f"{d.isoformat()}.docx"
        doc.save(docx_path)

        result = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(tmp_dir), str(docx_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        pdf_tmp_path = tmp_dir / f"{d.isoformat()}.pdf"
        if result.returncode != 0 or not pdf_tmp_path.exists():
            raise MagendaError(
                f"LibreOffice failed to render the {d.isoformat()} agenda to PDF "
                f"(exit {result.returncode}): {result.stderr or result.stdout}"
            )
        pdf_bytes = pdf_tmp_path.read_bytes()

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
