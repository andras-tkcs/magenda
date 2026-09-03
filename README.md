# Magenda

**Magenda** is a macOS MCP server that generates a daily agenda PDF, always
laid out exactly like `assets/template.docx`. There is no AI-generated
layout, and no LibreOffice (or any other external app) at runtime: every
tool edits a plain-data working agenda, and rendering assembles the PDF
directly in Python against a template compiled once ahead of time. Same
input, same bytes, every time.

---

## How it works

```
                     ONE-OFF, RUN BY HAND, NOT PART OF ANY BUILD
assets/template.docx ──▶ scripts/compile_template.py ──▶ assets/compiled/
 (hand-edited in Word/       (needs a local LibreOffice        (chrome.pdf +
  LibreOffice, as always)     install; see below)               slots.json)
                                                                       │
                                                          committed, like assets/fonts/
                                                                       │
────────────────────────────────────────────────────────────────────┼──────
                                            EVERY RENDER — no LibreOffice  │
Claude ──MCP stdio──▶ magenda                                             │
                          │                                               │
               ┌──────────▼──────────┐                                    │
               │  AgendaState          │  plain data — dates, tasks,      │
               │  (in-memory)          │  meetings, schedule entries      │
               └──────────┬──────────┘                                    │
                          │                                               │
               ┌──────────▼──────────┐                                    │
               │  pdf_assembler.py     │◀───────────────────────────────────┘
               │  pymupdf: clone page  │  clone chrome pages, insert text with
               │  shells + insert text │  the bundled .ttf handed directly to
               └──────────┬──────────┘  pymupdf — no OS font install either
                          │
                  pixel-identical PDF ──▶ you
```

Tools never generate or rewrite layout — they only inject plain text into
known slots (a date, a task, a meeting title) or add a page. The structure
is always fixed, but the font and 5 accent colors are configurable — see
[Look and feel](#look-and-feel).

`assets/compiled/` (the compiled template LibreOffice never needs to touch
again) is a checked-in build artifact, the same way `assets/fonts/` already
is — see [Compiling the template](#compiling-the-template) for when (rarely)
it needs regenerating.

---

## Tools

| Tool | Description |
|------|-------------|
| `create_agenda(date, meetings?, daily_schedule?, tasks?, delegated_tasks?, render?, include_base64?, output_dir?)` | Create a fresh agenda for `date` (`YYYY-MM-DD`), always starting from a blank template — an existing agenda for the same date is discarded and replaced. Optional args run the rest of the setup end-to-end in the same call: add every meeting in `meetings`, fill `daily_schedule`, append `tasks`, populate the delegated-tasks page(s) with `delegated_tasks`, and render to PDF if `render` is true. |
| `adjust_dates(date)` | Confirms the calendar header block and "next 4 weeks" grid for an existing agenda are in sync. There's nothing to actually recompute — both are derived live from the agenda's own date at render time, so they can't go stale between calls. |
| `add_meeting(date, title)` | Fill the first blank meeting slot, or append a new meeting page (title + ruled notes table), always as a single page. A title too long for one line is cut off at the end, never wrapped. |
| `add_daily_schedule(date, entries)` | Fill specific hour slots (`8am`..`6pm`) in the page-1 daily schedule. Each entry: `{hour, text}`. Text that doesn't fit is cut off at the end, never wrapped. |
| `add_tasks(date, tasks)` | Append tasks to the page-1 to-do list, filling empty rows top-down (18-row capacity). Each task: `{text, due}`. Long task text shrinks down to 9pt before wrapping across multiple lines. |
| `add_delegated_tasks(date, tasks)` | Add rows to the delegated-tasks page(s), one row per task: `{text, owner?, cadence, marked?, status?}` (`cadence` is `daily`\|`weekly`\|`monthly`; `marked` highlights the row green; `owner` is centered; `status` renders as a bullet list, one bullet per `\n`-separated line). Rows are numbered automatically in display order — not part of the task data. Merges with whatever's already on the page and re-sorts the full set — marked rows first, then unmarked, each group ordered daily → weekly → monthly — spilling onto as many pages as needed with no trailing empty row. The page itself only exists when there's at least one delegated task; `create_agenda` drops it otherwise. Its own page footer ("Notes and updates") has blank ruled lines for handwritten status updates. |
| `render_pdf(date, include_base64?, output_dir?)` | Render the working agenda to PDF. Pure Python — no subprocess, no LibreOffice. |

Working agendas live only in the server's memory, keyed by date — nothing
is written to disk. `render_pdf` (and `create_agenda`/`render=true`) is the
only exception: pass `output_dir` to also keep a persistent copy on disk;
otherwise the PDF is only returned as base64.

---

## Look and feel

The template's layout is always fixed — pages, tables, column widths never
change. Its font and 5 accent colors are configurable, though:

| Setting | Default | What it colors |
|---|---|---|
| Font pack | `outfit` | every text run in the template |
| Weekend color | `EE0000` | Saturday/Sunday weekday-header labels and dates |
| Date heading color | `215E99` | the big day/month/year heading (e.g. "19 TUESDAY"), in the page header |
| Section label color | `BF4E14` | section headers and table column headers (TO-DO LIST, DAILY SCHEDULE, Task & cadence/Owner/Status) and the delegated-tasks row numbers (delegated-tasks body text — task/owner/status — is always plain black, not themed) |
| Accent color | `3A7C22` | "Meeting title:" and the delegated-tasks page's own "Notes and updates" footer heading |
| Notes header color | `00B0F0` | the "Further notes from today" header |

Colors are hex `RRGGBB`. Font packs are a small **certified** set, not any
installed font name — a pack has to pass `scripts/certify_font_pack.py`
first (every tight static-label cell in the template still fits, and the
pack isn't measurably wider than Outfit at any matching weight) before it's
trusted here, so swapping fonts can't silently wrap or overflow the
template. Currently certified: `outfit` (default), `roboto`, `jetbrains_mono`
(monospace).

Every font is handed to the PDF library directly as a file
(`assets/fonts/*.ttf`) on every render — nothing is installed into the
machine's font directory, so there's no per-machine font-substitution risk
and no first-render side effect on your system.

Theming is resolved fresh on every render from whatever's currently
configured — see [pdf_assembler.py](src/magenda/pdf_assembler.py) — so a
themed render never affects what a later tool call for the same date sees.

**Claude Desktop (MCPB extension):** set these from the extension's own
Settings page (Claude Desktop → Settings → Extensions → magenda) — no config
file editing needed. Claude Desktop needs to be restarted (or the server
reconnected) for a change to take effect; it's read once at server startup.

**From source / Claude Code:** set the same options as environment variables
on the server's `mcpServers` entry:

```json
{
  "mcpServers": {
    "magenda": {
      "command": "/absolute/path/to/magenda/.venv/bin/magenda",
      "env": {
        "MAGENDA_FONT_PACK": "roboto",
        "MAGENDA_WEEKEND_COLOR": "DC2626",
        "MAGENDA_HEADING_COLOR": "7C3AED",
        "MAGENDA_LABEL_COLOR": "1E3A8A",
        "MAGENDA_ACCENT_COLOR": "047857",
        "MAGENDA_NOTES_COLOR": "0891B2"
      }
    }
  }
}
```

Any field can be omitted — an unset, unknown, or malformed value silently
falls back to that field's default rather than failing a render.

---

## Installation

### From the MCPB extension (recommended, Claude Desktop)

1. Download the latest `Magenda-x.y.z.mcpb` from the [Releases](../../releases) page (or build one yourself, see [Building the MCPB extension](#building-the-mcpb-extension)).
2. Double-click the `.mcpb` file (or drag it into Claude Desktop → Settings → Extensions). Claude Desktop installs and registers the server automatically — no config file editing needed, and nothing else to install.

### From source

**Requirements:** Python 3.11+, macOS

```bash
git clone https://github.com/andras-tkcs/magenda
cd magenda
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Register Magenda with Claude (see [MCP registration](#mcp-registration)) using the path `.venv/bin/magenda`.

---

## MCP registration

The MCPB extension (above) registers itself automatically in Claude Desktop.
The steps below are only needed for a from-source install, or for Claude
Code, which does not yet support one-click `.mcpb` installation.

### Claude Desktop (macOS), from source

Edit (or create) Claude Desktop's config file:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

```json
{
  "mcpServers": {
    "magenda": {
      "command": "/absolute/path/to/magenda/.venv/bin/magenda"
    }
  }
}
```

If the file already has other servers under `mcpServers`, just add the
`"magenda"` entry alongside them. Fully quit and reopen Claude Desktop after
saving for the new server to be picked up.

### Claude Code (project-level)

Add the same `command` to the project's `.claude/settings.json` under
`mcpServers`, or run:

```bash
claude mcp add magenda /absolute/path/to/magenda/.venv/bin/magenda
```

After registering, ask Claude to create an agenda (e.g. "create today's
agenda") to confirm the setup.

---

## Compiling the template

`assets/template.docx` is still the human-editable design source — open it
in Word/LibreOffice/Google Docs and edit it exactly as before. What changed
is what the *server* reads at runtime: not the docx itself, but
`assets/compiled/` — a chrome PDF (every page's borders/shading/ruled lines,
with every themable label left blank) plus `slots.json` (where each piece of
text goes, and in what role/size/weight) that `scripts/compile_template.py`
produces from it.

**This is a one-off step, not a build step.** It never runs in CI and never
runs automatically — `assets/compiled/` is committed to the repo (same
pattern as the pre-generated fonts under `assets/fonts/`, see
[Building the MCPB extension](#building-the-mcpb-extension)) and reused by
every render until it's regenerated by hand:

```bash
pip install -e ".[dev]"   # pulls in lxml, needed only by the compiler
python scripts/compile_template.py
```

Needs a local LibreOffice install (`brew install --cask libreoffice`) —
the *only* place LibreOffice is invoked anywhere in this project, and only
on the machine doing the recompiling. Run this, and commit the result,
only when `assets/template.docx`'s **structure** changes (a new slot, a
moved or resized table, a new page type). A content-only change (e.g. a
different default color) never needs it — that's already a runtime
[Look and feel](#look-and-feel) setting, not baked into the template.

See [docs/design/remove-libreoffice-runtime-dependency.md](docs/design/remove-libreoffice-runtime-dependency.md)
for the full design behind this split.

---

## Building the MCPB extension

```bash
pip install -e ".[dev]"
bash scripts/build_mcpb.sh
```

Building the `.mcpb` needs Node.js (used via `npx` to run the `mcpb` CLI —
`npm install -g @anthropic-ai/mcpb` also works and is picked up
automatically if present). It does **not** need LibreOffice — it just
packages the already-committed `assets/compiled/` and `assets/fonts/`.

Output: `dist/Magenda-<version>.mcpb`

Optional code signing of the bundled executable:

```bash
bash scripts/build_mcpb.sh --sign "Developer ID Application: Your Name (TEAMID)"
```

A tagged push (`vX.Y.Z`) to GitHub builds and attaches the `.mcpb` to a release automatically — see `.github/workflows/build.yml`.

The bundled font families (`assets/fonts/`) ship pre-generated, the same
"generate once, commit, reuse" pattern as `assets/compiled/` above. Each is
generated from its canonical Google Fonts variable font
(`scripts/font_source/`) via `python scripts/build_fonts.py [pack_id ...]`
(no args regenerates every pack). Adding a new pack means registering it in
`src/magenda/font_packs.py` and `scripts/build_fonts.py`, then running it
through `python scripts/certify_font_pack.py <pack_id>` before it's trusted
as a theming option — see [Look and feel](#look-and-feel). Unlike a
template-structure change, a new font pack never requires recompiling the
template.

---

## Manual testing

`scripts/manual_test.py` builds a realistic agenda for **today** end-to-end
(meetings, schedule, tasks) and renders it to PDF for visual inspection:

```bash
python scripts/manual_test.py
```

It prints the resulting PDF path — open it and check the layout against
`assets/template.docx`.

`scripts/manual_test_themed.py` does the same, then renders the result again
under every certified font pack (via `pdf_assembler.assemble`) so they can
be compared side by side against the Outfit baseline.

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```

`tests/compiler/` exercises `scripts/compiler/` (the OOXML-editing code
`scripts/compile_template.py` uses) and needs the `lxml` dev dependency;
everything else tests the runtime package and needs nothing beyond the base
install.
