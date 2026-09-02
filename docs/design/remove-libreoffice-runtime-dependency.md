# Design: remove the LibreOffice runtime dependency

Status: **design only — not implemented**. This document describes a
proposed architecture; no production code changes accompany it.

## 1. Problem

Every render today shells out to headless LibreOffice
(`src/magenda/soffice.py`, `src/magenda/tools/render.py`,
`src/magenda/theme.py::render_pdf_with_theme`) to turn a docx built by
`xml_ops.py` into the PDF Claude hands back. That means:

- **An external binary is a hard runtime requirement.** The README tells
  every user to `brew install --cask libreoffice` before the extension
  works at all (README.md:120, manifest.json's `long_description`). It's
  ~1GB+, macOS-only in practice here, and a common source of "it doesn't
  work" reports that have nothing to do with Magenda's own code.
- **Fonts have to be installed system-wide before every render**
  (`font_setup.ensure_fonts_installed`, called from both `render.py` and
  `theme.py`) purely so LibreOffice's font matching finds them. That's a
  side effect on the user's machine (files copied into
  `~/Library/Fonts` or `~/.local/share/fonts`, `fc-cache` invoked) that
  has nothing to do with what the tool call actually asked for, and it's
  a second, independent way rendering can drift between machines if font
  matching ever falls back.
- **Rendering is slow and heavyweight** for what is, structurally,
  already a fully-determined layout — see §2.

## 2. The key observation: LibreOffice isn't doing layout work anymore

Reading `xml_ops.py` closely, essentially none of the *decisions* in a
render are actually left to Word/LibreOffice at render time:

- Meeting pages are appended by literally cloning the one-page XML unit
  and inserting a page break (`insert_meeting_page`, xml_ops.py:600) —
  the page count is decided in Python before any conversion happens.
- Delegated-tasks pagination is fully computed in Python: row capacity
  per page is a constant (`DELEGATED_ROWS_PER_PAGE = 8`, xml_ops.py:708,
  picked to survive worst-case wrapping) and `rebuild_delegated_tasks`
  (xml_ops.py:1173) clones a new page-shell the moment a page fills up.
  Nothing is left for Word to auto-flow.
- Text fitting — truncation, shrink-then-wrap, how many lines a block
  needs — is already independently computed against the *actual* font
  metrics via Pillow (`text_fit.py`), not by asking LibreOffice to lay it
  out and seeing what happens.
- Table structure (column widths, borders, shading, cell merges) is
  static template content that's cloned verbatim, never generated.

So by the time `render.py` calls `soffice --convert-to pdf`, the only
things LibreOffice is actually contributing are: (a) shaping glyphs onto
the page, (b) painting the already-decided borders/shading, and (c)
producing PDF bytes with fonts embedded. That's a rendering step with
zero remaining decisions — which is exactly the kind of step you can
precompute once and stamp values into afterward, instead of re-running
end-to-end on every call.

## 3. Goals

- No subprocess, no external binary, no runtime font installation. Pure
  Python (`pymupdf` + `Pillow`, both already dependencies) at runtime.
- Byte-for-byte-in-spirit determinism preserved: same input, same visual
  output, regardless of host machine.
- `assets/template.docx` stays the human-editable source of truth for
  layout — designers keep editing it in Word/LibreOffice/Google Docs
  exactly as today. Nothing about *authoring* the template changes.
- Converting that docx into whatever the runtime actually consumes
  becomes a **one-off, manual, developer-run step** — not a build step,
  not a CI step, not something that runs per-release. It reruns only
  when the template's *structure* changes (new slot, new page type,
  moved table, resized column) and its output is committed to the repo
  like any other generated asset already is (see §8 — this repo already
  has exactly this pattern for fonts).

## 4. Non-goals

- Redesigning the agenda's visual layout. Out of scope — this is a
  backend swap, the PDF should look the same.
- Removing `assets/template.docx` or the ability to hand-edit it.
- Solving accessibility/tagged-PDF parity with LibreOffice's export (see
  §11, flagged as a known gap, not addressed here).
- Changing the MCP tool surface (`create_agenda`, `add_meeting`, etc.) —
  same inputs/outputs, different internals.

## 5. New architecture

```
                         ONE-OFF, MANUAL, LOCAL — not CI, not a build step
                         ┌─────────────────────────────────────────────┐
assets/template.docx ──▶ │ scripts/compile_template.py                 │
(hand-edited, as today)  │   - annotates every dynamic slot with a     │
                         │     unique sentinel token                    │
                         │   - renders once via local LibreOffice      │
                         │   - locates every sentinel in the rendered  │
                         │     PDF (pymupdf.search_for), records its   │
                         │     rect/font-role/size/color                │
                         │   - extracts cloneable page/row fragments   │
                         │     (meeting-page unit, delegated-row unit) │
                         └──────────────────┬────────────────────────┘
                                            │ writes, committed to git:
                                            ▼
                         assets/compiled/template.pdf   (blank shells,
                                                          sentinels stripped)
                         assets/compiled/slots.json      (geometry manifest)
                         assets/compiled/template.docx.sha256

────────────────────────────────────────────────────────────────────────
                         EVERY RUNTIME RENDER — no LibreOffice, no subprocess
Claude ──MCP stdio──▶ magenda
                          │
               ┌──────────▼──────────┐
               │  AgendaState          │   plain-data model: dates, filled
               │  (dataclasses)        │   schedule slots, tasks, meetings,
               └──────────┬──────────┘   delegated tasks — no XML at all
                          │
               ┌──────────▼──────────┐
               │  pdf_assembler.py     │   pymupdf: clone page/row shells
               │  state + slots.json   │   from assets/compiled/template.pdf,
               │  → PDF bytes          │   insert_text() with bundled TTFs
               └──────────┬──────────┘   directly (no OS font install)
                          │
               ┌──────────▼──────────┐
               │  pdf_links.py          │   UNCHANGED — already PDF-native
               └──────────┬──────────┘
                          │
                  pixel-identical PDF ──▶ you
```

## 6. The compiled-template bundle (new internal storage format)

This is the "docx → internal storage" conversion the task asks for.
Proposed contents of `assets/compiled/`, all committed to git:

- **`template.pdf`** — the rendered template with every dynamic slot
  left visually blank, containing:
  - The four fixed page shells in document order: overview (page 1),
    delegated-tasks page shell (header row + `DELEGATED_ROWS_PER_PAGE`
    blank ruled rows), one meeting-page unit (title + ruled notes
    table), the closing "Further notes" page — the same four structural
    units `xml_ops.py` already treats as the building blocks of a
    document (`find_meeting_unit_template`, `_build_delegated_table_shell`,
    `find_further_notes_paragraph`).
  - Each shell page's header/footer chrome baked in as normal PDF page
    content, because that's what a docx→PDF conversion already does —
    Word's single `header1.xml`/`footer*.xml` parts get instantiated
    once per physical page in the output, so every shell page in
    `template.pdf` already carries its own header/footer, no extra work
    needed to "repeat" it at runtime.
- **`slots.json`** — the geometry manifest: for every dynamic slot, its
  owning shell page index, its PDF-space rect (points, from the
  sentinel's `search_for` hit), a **role** rather than a literal font
  name (`"heading"`, `"label"`, `"accent"`, `"body"`, `"weekend"`,
  `"notes"`, matching the roles `theme.py` already defines in
  `_ORIGINAL_COLORS`), a base font weight bucket
  (`thin`/`extralight`/`regular`/`semibold`/`black`, matching
  `font_packs.WEIGHT_BUCKETS`), a point size, an alignment, and a
  max-width (for `text_fit.py`-style truncation/wrapping). Slot ids are
  stable strings (`"schedule.8am"`, `"todo.row.03"`,
  `"delegated.row.00.task"`, `"header.day"`, `"meeting.title"`, ...) so
  runtime code addresses slots by name, never by coordinates it derived
  itself.
- **`template.docx.sha256`** — hash of the `assets/template.docx` that
  produced this bundle, so CI (or a pre-commit hook) can cheaply assert
  the checked-in compiled bundle still matches the checked-in docx,
  without needing LibreOffice to do the check (see §9).

Why sentinel-token search rather than computing rects analytically from
the docx's own twips-based geometry: table row heights, cell padding,
and vertical centering depend on the text layout engine's own metrics
(line height, leading) in ways `text_fit.py` only approximates for
*width*, not full paragraph/table layout. Trusting one real rendering
pass as ground truth (the same trick `pdf_links.py` already uses to find
the "<< Overview"/"Notes >>" header labels via `search_for`, rather than
computing their position) is lower-risk than re-deriving Word's table
layout math from scratch.

## 7. Cloneable units: pages and rows, without a layout engine

Two things get cloned at runtime today (`insert_meeting_page`,
`rebuild_delegated_tasks`'s per-page-overflow cloning). In the new model:

- **Whole pages** (a new meeting page, a new delegated-tasks page when
  the previous one fills up): `pymupdf.Document.insert_pdf(src, from_page,
  to_page, start_at=...)` copies a full page — including embedded fonts
  and vector content — from `template.pdf`'s meeting-unit or
  delegated-page-shell page into the assembled document. No text shaping
  happens here at all; it's a structural copy.
- **Individual delegated-task rows**: rather than trying to clone
  sub-page fragments, keep the row's static chrome (borders, the
  thick-top-of-page vs thin-between-rows border, the marked/unmarked
  green fill) as **vector drawing** at runtime —
  `page.draw_rect`/`draw_line` using the exact same constants xml_ops.py
  already has (`_THICK_BORDER_SZ`, `_THIN_BORDER_SZ`,
  `DELEGATED_MARK_FILL`, converted from twips to points once). This is
  simpler and more robust than fragment-copying for what are, after all,
  just rectangles, and it sidesteps needing four separate captured row
  variants (thick/thin × marked/unmarked) from the compiler.

Text — meeting titles, schedule entries, to-do rows, delegated-task
text/owner/status, the calendar header numbers — is never copied from
the template; it's inserted fresh every render via
`page.insert_text()`/`insert_textbox()`, with the bundled `.ttf` handed
in directly via `fontfile=`. That's what removes the font problem: the
font bytes travel with the call, there's no dependency on what's
installed on the machine, and no install step to run first.

## 8. Precedent already in this repo for "compile once, commit, reuse"

This isn't a new pattern for the project. `assets/fonts/*.ttf` are
already generated once from `scripts/font_source/*-Variable.ttf` via
`scripts/build_fonts.py`, committed as binary assets, and never
regenerated by CI or `build_mcpb.sh` — the README says so explicitly
(README.md:205-213: "ship pre-generated ... regenerate with
`python scripts/build_fonts.py`"). `scripts/certify_font_pack.py` is the
same shape again: a manual, occasionally-run gate, not a build step.

`scripts/compile_template.py` (new) is the same pattern applied to the
docx template: run locally, by hand, only when `assets/template.docx`'s
*structure* changes; its output (`assets/compiled/`) is committed and
is what every build and every runtime render actually uses. A content
edit that doesn't touch structure (e.g. changing a default color, which
already happens at render time via `theme.py`, not in the template)
never requires recompiling.

## 9. Guarding against a stale compiled bundle

Since recompiling is manual, add one cheap, LibreOffice-free CI check:
a step in `.github/workflows/build.yml` (or a pre-commit hook) that
hashes the committed `assets/template.docx` and compares it to
`assets/compiled/template.docx.sha256`. Mismatch fails the build with a
clear message ("template.docx changed — run
`scripts/compile_template.py` and commit the result"). This is the only
new CI-side check; it needs no LibreOffice, just `sha256sum`.

## 10. Runtime module changes

| Today | Replaced by |
|---|---|
| `agenda_store.py` (`AgendaDocument` = parsed docx XML tree, one per date, process-lifetime) | `AgendaState` — a small dataclass tree (dates, filled schedule slots, task rows, meeting titles, delegated tasks) with no XML at all. Same storage semantics (in-memory, keyed by date, lost on restart) — see agenda_store.py:42-44's own docstring, unchanged. |
| `xml_ops.py` (1229 lines: OOXML node mutation, cloning, text fitting glue, pagination bookkeeping) | Mostly deleted. The pagination *constants and logic that stay meaningful* (row capacity, cadence ordering, header labels) move into a much smaller `agenda_layout.py` operating on `AgendaState`. The OOXML-specific mutation code (namespace juggling, `w:tc`/`w:tr` construction, vmerge) goes away entirely — there's no XML tree to mutate anymore. |
| `soffice.py`, `tools/render.py`'s subprocess call, `theme.py::render_pdf_with_theme`'s subprocess call | `pdf_assembler.py`: `AgendaState + Theme + assets/compiled/{template.pdf,slots.json} → PDF bytes`, pure pymupdf, no subprocess. |
| `font_setup.py` (install into OS font dir + `fc-cache`) | Deleted. Fonts are only ever embedded per-call via `insert_text(..., fontfile=...)`; nothing is installed system-wide. |
| `theme.py`'s font-pack/color substitution over `w:rFonts`/`w:color` | Same idea, cheaper: since text is inserted fresh at render time anyway, theming is just "which `.ttf` and which hex value does this slot's *role* map to" — no substitution pass over an existing tree needed. |
| `pdf_links.py` | **Unchanged.** It already operates purely on the assembled PDF via `pymupdf`/`search_for`, independent of how that PDF was produced. |
| `text_fit.py` | **Unchanged.** Font-metric-based fitting is exactly as useful for "does this line fit in this slot's rect" as it is today. |

Each MCP tool (`create_agenda`, `add_meeting`, `add_daily_schedule`,
`add_tasks`, `add_delegated_tasks`, `adjust_dates`, `render_pdf`) keeps
its existing signature and behavior; only what it mutates changes
(`AgendaState` fields instead of XML nodes), and `render_pdf` calls
`pdf_assembler.assemble(state, theme)` instead of shelling out.

## 11. Known gaps / risks to validate before implementing

- **Text-layout fidelity.** LibreOffice's own line-breaking/kerning for
  wrapped lines (to-do items, delegated-task status bullets) may differ
  subtly from a hand-rolled wrap using `text_fit.fit_downsize_or_wrap`'s
  Pillow-measured breakpoints, even though that function already exists
  and is already trusted for width decisions today. Needs a visual diff
  pass (render the same realistic agenda both ways, compare) before
  cutting over — `scripts/manual_test.py`/`manual_test_themed.py` are
  the natural harness for this.
- **Accessibility/structure.** A LibreOffice-exported PDF may carry more
  structural/tagging information than a hand-assembled one built from
  copied page fragments plus inserted text. Low priority for a personal
  daily-planner PDF, but worth naming explicitly rather than silently
  regressing it.
- **Sentinel collisions.** The compiler's slot-detection depends on each
  sentinel token being findable exactly once per shell page
  (`search_for` returning exactly one hit) — the same fragility
  `pdf_links.py` already accepts and handles by skipping ambiguous
  matches (add_meeting_links, pdf_links.py:56-59). Sentinel strings
  should be chosen to make collisions structurally impossible (e.g.
  `"§SLOT:schedule.8am§"`), not just unlikely.
- **New font packs / new certified fonts** still only need
  `scripts/build_fonts.py` + `scripts/certify_font_pack.py` as today —
  unaffected by this change, since font embedding is per-call regardless
  of which `.ttf` is chosen.
- **Platform scope.** `manifest.json`'s `"platforms": ["darwin"]`
  restriction was likely there partly because of `soffice.py`'s
  macOS-specific candidate paths; `font_setup._user_font_dir` already
  has Linux/Windows branches. Worth revisiting whether this change lets
  the extension's platform scope widen — not required for this design,
  but a nice side effect to flag.

## 12. Rollout plan

1. Build `scripts/compile_template.py` and the `assets/compiled/`
   format; validate it round-trips the *current* template with a visual
   diff against today's LibreOffice output. No runtime code changes yet
   — this phase is purely additive and low-risk.
2. Build `pdf_assembler.py` + `AgendaState` behind the existing tool
   functions, one tool at a time (start with `render_pdf`/`create_agenda`
   with no content, then layer in schedule/tasks/meetings/delegated
   tasks), keeping the LibreOffice path available for A/B comparison
   until every tool's output matches.
3. Cut `render_pdf`/`create_agenda` over to the new path; delete
   `soffice.py`, `font_setup.py`'s install logic, and the OOXML-mutation
   parts of `xml_ops.py`/`agenda_store.py`.
4. Update `README.md` (drop the LibreOffice install step entirely),
   `manifest.json`'s `long_description`, and add the staleness check
   from §9 to `.github/workflows/build.yml`.
5. Bump the minor version; call out "no external dependencies at
   runtime" as the headline change.

Each phase is independently revertable, and phase 1 in particular can
happen — and be reviewed — entirely before any decision to proceed
further is made.
