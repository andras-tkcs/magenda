import base64
import shutil
import subprocess
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
    pixel-identical regardless of which machine renders it. By default the
    PDF is written next to the agenda store; pass `output_dir` to write it
    somewhere else instead (the directory is created if it doesn't exist)."""
    d = parse_date(date)
    if not agenda_store.docx_exists(d):
        raise MagendaError(f"no agenda exists for {d.isoformat()} yet; call create_agenda first")

    font_setup.ensure_fonts_installed()

    docx_path = agenda_store.docx_path(d)
    out_dir = Path(output_dir).expanduser() if output_dir else agenda_store.AGENDA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    soffice = _find_soffice()

    result = subprocess.run(
        [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    pdf_path = out_dir / f"{d.isoformat()}.pdf"
    if result.returncode != 0 or not pdf_path.exists():
        raise MagendaError(
            f"LibreOffice failed to render {docx_path} to PDF "
            f"(exit {result.returncode}): {result.stderr or result.stdout}"
        )

    response = {"date": d.isoformat(), "path": str(pdf_path)}
    if include_base64:
        response["pdf_base64"] = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    return response
