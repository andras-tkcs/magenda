"""Certify a font pack (see src/magenda/font_packs.py) against
assets/template.docx before it's trusted as a theming option.

Two checks, both against the template's own tightest cells:

1. Static-label fit — every fixed-width table cell's text (weekday headers,
   section titles, column headers, ...) must still render within its cell at
   the template's own point size, with a safety margin. Ground truth for
   "what does this actually render as" comes from LibreOffice's own output:
   the template is rendered to PDF and each line's real per-span sizes are
   read back out of that PDF (via PyMuPDF), rather than re-derived from
   document.xml by hand — OOXML run-property inheritance is easy to get
   wrong (a run with no explicit w:sz doesn't necessarily inherit its
   *paragraph's* rPr the way it looks like it should — an early version of
   this check got exactly that case wrong), so the rendered PDF is
   authoritative, not a reading of the XML.

2. Not wider than Outfit — the candidate must not be measurably wider than
   Outfit at any matching weight, across a generic alphanumeric sample. This
   is what keeps *dynamic* content (to-do tasks, meeting titles, schedule
   entries) safe: their shrink-to-fit/wrap decisions (text_fit.py) run when
   content is added, against whatever font is in the *working* document at
   that time — which is always Outfit, since theme.apply_font_pack only ever
   touches a render-time clone, never the shared in-memory working doc (see
   theme.py). A pack that's never wider than Outfit can't invalidate a fit
   already computed against it.

A cell that fails check 1 isn't necessarily a dead end — see the MON/WED
fix in git history (widening a column instead of rejecting Outfit itself)
for the template-side alternative to rejecting the pack.

Run:
    python scripts/certify_font_pack.py roboto
    python scripts/certify_font_pack.py roboto jetbrains_mono
    python scripts/certify_font_pack.py --all
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import pymupdf  # noqa: E402
from PIL import ImageFont  # noqa: E402

from magenda import agenda_store, font_setup  # noqa: E402
from magenda.font_packs import FONT_PACKS, WEIGHT_BUCKETS  # noqa: E402
from magenda.paths import FONTS_DIR, TEMPLATE_PATH  # noqa: E402
from magenda.xml_ops import NS, cell_text_width_twips, qn  # noqa: E402

SAFETY_MARGIN_TWIPS = 20  # cushion for PIL-vs-LibreOffice layout-engine mismatch
GENERIC_SAMPLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 &-.,:"
WIDTH_TOLERANCE_PCT = 100.5  # candidate may be up to this much of Outfit's width

# Maps a PDF-embedded font's PostScript-ish name (as PyMuPDF reports it) back
# to the template's own weight buckets. Only needs to cover Outfit, since
# certification always renders the *unmodified* template.
_OUTFIT_FONT_TO_BUCKET = {
    "outfitthin": "thin",
    "outfitextralight": "extralight",
    "outfitsemibold": "semibold",
    "outfitblack": "black",
    "outfit": "regular",
}


def _bucket_for_pdf_font(fontname: str) -> str | None:
    fn = fontname.lower().replace(" ", "").replace("-", "")
    for key, bucket in sorted(_OUTFIT_FONT_TO_BUCKET.items(), key=lambda kv: -len(kv[0])):
        if key in fn:
            return bucket
    return None


def _find_soffice() -> str:
    for candidate in (
        "soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/opt/homebrew/bin/soffice",
        "/usr/bin/soffice",
    ):
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    raise SystemExit(
        "LibreOffice ('soffice') was not found. Install it (e.g. `brew install --cask "
        "libreoffice`) to run certification."
    )


def _render_template_pdf(out_dir: Path) -> Path:
    font_setup.ensure_fonts_installed()
    soffice = _find_soffice()
    result = subprocess.run(
        [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(out_dir), str(TEMPLATE_PATH)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    pdf_path = out_dir / (TEMPLATE_PATH.stem + ".pdf")
    if result.returncode != 0 or not pdf_path.exists():
        raise SystemExit(
            f"LibreOffice failed to render the template (exit {result.returncode}): "
            f"{result.stderr or result.stdout}"
        )
    return pdf_path


def _verified_rows(pdf_path: Path) -> list[tuple[str, int, list[tuple[str, float, str]]]]:
    """Every fixed-width table cell's text, matched to its actually-rendered
    PDF line and decomposed into (span_text, size_pt, weight_bucket) — each
    span measured at the size LibreOffice really used."""
    pdf = pymupdf.open(str(pdf_path))
    pdf_lines: dict[str, list] = {}
    for page in pdf:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line["spans"]
                full = "".join(s["text"] for s in spans).strip()
                if not full:
                    continue
                pdf_lines.setdefault(full, []).append(
                    [(s["text"], round(s["size"], 1), s["font"]) for s in spans]
                )

    doc = agenda_store.AgendaDocument.load(TEMPLATE_PATH)
    rows: "OrderedDict[str, int]" = OrderedDict()
    for tc in doc.body.iter(qn("w:tc")):
        tcW = tc.find("w:tcPr/w:tcW", NS)
        if tcW is None:
            continue
        p = tc.find("w:p", NS)
        if p is None:
            continue
        runs = p.findall("w:r", NS)
        text = "".join(
            (r.find("w:t", NS).text or "") for r in runs if r.find("w:t", NS) is not None
        ).strip()
        if not text:
            continue
        try:
            avail = cell_text_width_twips(tc)
        except Exception:
            continue
        rows[text] = avail

    verified: list[tuple[str, int, list[tuple[str, float, str]]]] = []
    for text, avail in rows.items():
        candidates = pdf_lines.get(text)
        if not candidates:
            continue
        decomposed = []
        ok = True
        for span_text, size_pt, fontname in candidates[0]:
            bucket = _bucket_for_pdf_font(fontname)
            if bucket is None:
                ok = False
                break
            decomposed.append((span_text, size_pt, bucket))
        if ok:
            verified.append((text, avail, decomposed))
    return verified


_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# FreeType hints glyph outlines to the integer pixel grid at the point size
# it's asked to rasterize at — measuring directly at (rounded) nominal sizes
# can make two adjacent integer point sizes report the *same* bbox width for
# a monospace font, silently masking a real sub-point scale difference (a
# candidate rendered at 10.8pt vs 12pt came back bit-identical). Rendering
# at _MEASURE_SCALE times the size and dividing back down measures against a
# much finer grid, so fractional-point differences (e.g. a pack's
# size_scale) actually show up.
_MEASURE_SCALE = 10


def _load_font(path: Path, scaled_size_pt: int) -> ImageFont.FreeTypeFont:
    key = (str(path), scaled_size_pt)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(path), scaled_size_pt)
    return _font_cache[key]


def _width_twips(text: str, path: Path, size_pt: float) -> float:
    font = _load_font(path, max(1, round(size_pt * _MEASURE_SCALE)))
    bbox = font.getbbox(text)
    width_pt = (bbox[2] - bbox[0]) / _MEASURE_SCALE
    return width_pt * 20


class CertificationResult:
    def __init__(self, pack_id: str):
        self.pack_id = pack_id
        self.label_fails: list[tuple[str, int, float, float]] = []
        self.label_total = 0
        self.width_report: list[tuple[str, float, float, float]] = []

    @property
    def label_pass(self) -> int:
        return self.label_total - len(self.label_fails)

    @property
    def width_ok(self) -> bool:
        return all(pct <= WIDTH_TOLERANCE_PCT for _, _, _, pct in self.width_report)

    @property
    def ok(self) -> bool:
        return not self.label_fails and self.width_ok


def certify(pack_id: str, rows: list[tuple[str, int, list[tuple[str, float, str]]]]) -> CertificationResult:
    if pack_id not in FONT_PACKS:
        raise SystemExit(f"unknown font pack {pack_id!r}; available: {sorted(FONT_PACKS)}")
    pack = FONT_PACKS[pack_id]
    outfit = FONT_PACKS["outfit"]
    # theme.apply_font_pack shrinks every run this pack touches by
    # size_scale (see font_packs.py) — certify against what actually gets
    # rendered, not the pack's raw glyph metrics at Outfit's own sizes.
    size_scale = pack.get("size_scale", 1.0)
    result = CertificationResult(pack_id)
    result.label_total = len(rows)

    for text, avail, decomposed in rows:
        needed = sum(
            _width_twips(span_text, FONTS_DIR / pack["files"][bucket], size_pt * size_scale)
            for span_text, size_pt, bucket in decomposed
        )
        margin = avail - needed - SAFETY_MARGIN_TWIPS
        if margin < 0:
            result.label_fails.append((text, avail, needed, margin))

    for bucket in WEIGHT_BUCKETS:
        base = _width_twips(GENERIC_SAMPLE, FONTS_DIR / outfit["files"][bucket], 12)
        cand = _width_twips(GENERIC_SAMPLE, FONTS_DIR / pack["files"][bucket], 12 * size_scale)
        result.width_report.append((bucket, base, cand, cand / base * 100))

    return result


def print_report(result: CertificationResult) -> None:
    pack = FONT_PACKS[result.pack_id]
    scale = pack.get("size_scale", 1.0)
    print(f"=== {pack['display_name']} ({result.pack_id}) ===")
    if scale != 1.0:
        print(f"size_scale: {scale}")
    print(f"static-label fit: {result.label_pass}/{result.label_total} pass "
          f"(margin {SAFETY_MARGIN_TWIPS}tw)")
    for text, avail, needed, margin in sorted(result.label_fails, key=lambda f: f[3]):
        print(f"  FAIL {text!r:35} avail={avail:5d} needed={needed:6.0f} short by {-margin:5.0f}tw")
    print(f"width vs Outfit (generic sample, 12pt, tolerance {WIDTH_TOLERANCE_PCT}%):")
    for bucket, base, cand, pct in result.width_report:
        flag = "" if pct <= WIDTH_TOLERANCE_PCT else "  !"
        print(f"  {bucket:12} {pct:6.1f}%{flag}")
    note = pack.get("notes")
    if note:
        print(f"note: {note}")
    print("PASS" if result.ok else "FAIL", "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("packs", nargs="*", help="font pack id(s) to certify")
    parser.add_argument("--all", action="store_true", help="certify every pack in the registry")
    args = parser.parse_args()

    if args.all:
        pack_ids = list(FONT_PACKS)
    elif args.packs:
        pack_ids = args.packs
    else:
        parser.error("pass one or more pack ids, or --all")

    with tempfile.TemporaryDirectory(prefix="magenda-certify-") as tmp:
        pdf_path = _render_template_pdf(Path(tmp))
        rows = _verified_rows(pdf_path)

    print(f"{len(rows)} static-label rows verified against the rendered template\n")

    results = [certify(pid, rows) for pid in pack_ids]
    for result in results:
        print_report(result)

    failed = [r.pack_id for r in results if not r.ok]
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
