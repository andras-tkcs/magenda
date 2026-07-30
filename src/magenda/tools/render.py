import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

from magenda import agenda_store, font_setup
from magenda.tools._common import parse_date
from magenda.xml_ops import MagendaError

_SOFFICE_CANDIDATES = [
    "soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/bin/soffice",
]


def _find_soffice() -> str:
    for candidate in _SOFFICE_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    raise MagendaError(
        "LibreOffice ('soffice') was not found. Install it (e.g. `brew install --cask "
        "libreoffice` on macOS) so agendas can be rendered to PDF deterministically."
    )


def render_pdf(date: str, include_base64: bool = False, output_dir: str | None = None) -> dict:
    """Render the working docx for `date` to PDF via headless LibreOffice,
    after ensuring the bundled Outfit fonts are installed so the output is
    pixel-identical regardless of which machine renders it. The conversion
    itself happens in a throwaway temp directory that's removed as soon as
    this call returns. By default nothing is left on disk -- the PDF bytes
    come back as base64. Pass `output_dir` to also write a persistent copy
    there instead (the directory is created if it doesn't exist)."""
    d = parse_date(date)
    doc = agenda_store.load(d)

    font_setup.ensure_fonts_installed()
    soffice = _find_soffice()

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
