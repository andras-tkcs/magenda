# Magenda

**Magenda** is a macOS MCP server that generates a daily agenda PDF, always
laid out exactly like `assets/template.docx`. There is no AI-generated
layout: every tool edits a fixed set of XML nodes in a copy of the template
(set text, clone a pre-formatted subtree), and a pinned LibreOffice headless
build renders the result to PDF. Same input, same bytes, every time.

---

## How it works

```
Claude ──MCP stdio──▶ magenda
                          │
               ┌──────────▼──────────┐
               │  lxml                │
               │  edit fixed XML nodes│
               │  in a docx copy      │
               └──────────┬──────────┘
                          │
               ┌──────────▼──────────┐
               │  LibreOffice headless│
               │  docx → PDF          │
               └──────────┬──────────┘
                          │
                  pixel-identical PDF ──▶ you
```

Tools never generate or rewrite layout — they only inject plain text into
known slots (a date, a task, a meeting title) or clone a pre-formatted page.
Fonts are bundled and installed automatically so rendering is identical
regardless of which machine runs it. The structure is always fixed, but the
font and 4 accent colors are configurable — see [Look and feel](#look-and-feel).

---

## Tools

| Tool | Description |
|------|-------------|
| `create_agenda(date, meetings?, daily_schedule?, tasks?, delegated_tasks?, render?, include_base64?, output_dir?)` | Create a fresh agenda for `date` (`YYYY-MM-DD`), always starting from a blank template — an existing agenda for the same date is discarded and replaced. Optional args run the rest of the setup end-to-end in the same call: refresh calendar blocks, add every meeting in `meetings`, fill `daily_schedule`, append `tasks`, populate the delegated-tasks page(s) with `delegated_tasks`, and render to PDF if `render` is true. |
| `adjust_dates(date)` | Refresh every calendar header/footer block and the "next 4 weeks" grid for an existing agenda. |
| `add_meeting(date, title)` | Fill the first blank meeting slot, or clone and append a new meeting page (calendar header + title + ruled notes table), always as a single page. A title too long for one line is cut off at the end, never wrapped. |
| `add_daily_schedule(date, entries)` | Fill specific hour slots (`8am`..`6pm`) in the page-1 daily schedule. Each entry: `{hour, text}`. Text that doesn't fit is cut off at the end, never wrapped. |
| `add_tasks(date, tasks)` | Append tasks to the page-1 to-do list, filling empty rows top-down (18-row capacity). Each task: `{text, due}`. Long task text shrinks down to 9pt before wrapping across multiple lines. |
| `add_delegated_tasks(date, tasks)` | Add rows to the delegated-tasks page(s), one row per task: `{text, owner?, cadence, marked?, status?}` (`cadence` is `daily`\|`weekly`\|`monthly`; `marked` highlights the row green; `owner` is centered; `status` renders as a bullet list, one bullet per `\n`-separated line). Merges with whatever's already on the page and re-sorts the full set — marked rows first, then unmarked, each group ordered daily → weekly → monthly — spilling onto as many pages as needed with no trailing empty row. The page itself only exists when there's at least one delegated task; `create_agenda` drops it otherwise. |
| `render_pdf(date, include_base64?, output_dir?)` | Render the working docx to PDF via headless LibreOffice. |

Working agendas live only in the server's memory, keyed by date — nothing
is written to disk. `render_pdf` (and `create_agenda`/`render=true`) is the
only exception: it converts through a throwaway temp directory that's
deleted as soon as the call returns, and returns the PDF as base64. Pass
`output_dir` to also keep a persistent copy on disk.

---

## Look and feel

The template's layout is always fixed — pages, tables, column widths never
change. Its font and 4 accent colors are configurable, though:

| Setting | Default | What it colors |
|---|---|---|
| Font pack | `outfit` | every text run in the template |
| Weekend color | `EE0000` | Saturday/Sunday weekday-header labels and dates |
| Date heading color | `0DB04B` | the big day/month/year heading (e.g. "19 TUESDAY") |
| Section label color | `F95738` | section headers and table column headers (TO-DO LIST, DAILY SCHEDULE, Task/Owner/Status, "Meeting title:") |
| Notes header color | `FFCB47` | the "Further notes from today" header |

Colors are hex `RRGGBB`. Font packs are a small **certified** set, not any
installed font name — a pack has to pass `scripts/certify_font_pack.py`
first (every tight static-label cell in the template still fits, and the
pack isn't measurably wider than Outfit at any matching weight) before it's
trusted here, so swapping fonts can't silently wrap or overflow the
template. Currently certified: `outfit` (default), `roboto`, `jetbrains_mono`
(monospace).

Theming only affects the *rendered PDF* — the in-memory working agenda
itself always keeps the template's original Outfit-named runs, so later
tool calls for the same date are unaffected by whatever's configured.

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
2. Install LibreOffice — Magenda shells out to it to render PDFs, it is not bundled:

   ```bash
   brew install --cask libreoffice
   ```
3. Double-click the `.mcpb` file (or drag it into Claude Desktop → Settings → Extensions). Claude Desktop installs and registers the server automatically — no config file editing needed.

### From source

**Requirements:** Python 3.11+, macOS, [LibreOffice](https://www.libreoffice.org/) (`brew install --cask libreoffice`)

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

## Building the MCPB extension

```bash
pip install -e ".[dev]"
bash scripts/build_mcpb.sh
```

Building the `.mcpb` needs Node.js (used via `npx` to run the `mcpb` CLI —
`npm install -g @anthropic-ai/mcpb` also works and is picked up
automatically if present).

Output: `dist/Magenda-<version>.mcpb`

Optional code signing of the bundled executable:

```bash
bash scripts/build_mcpb.sh --sign "Developer ID Application: Your Name (TEAMID)"
```

A tagged push (`vX.Y.Z`) to GitHub builds and attaches the `.mcpb` to a release automatically — see `.github/workflows/build.yml`.

The bundled font families (`assets/fonts/`) ship pre-generated and are
installed into the user's font directory automatically before the first
render — see `src/magenda/font_setup.py`. Each is generated from its
canonical Google Fonts variable font (`scripts/font_source/`) via
`python scripts/build_fonts.py [pack_id ...]` (no args regenerates every
pack). Adding a new pack means registering it in `src/magenda/font_packs.py`
and `scripts/build_fonts.py`, then running it through
`python scripts/certify_font_pack.py <pack_id>` before it's trusted as a
theming option — see [Look and feel](#look-and-feel).

---

## Manual testing

`scripts/manual_test.py` builds a realistic agenda for **today** end-to-end
(meetings, schedule, tasks) and renders it to PDF for visual inspection:

```bash
python scripts/manual_test.py
```

It prints the resulting PDF path — open it and check the layout against
`assets/template.docx` converted to PDF.

`scripts/manual_test_themed.py` does the same, then renders the result again
under every certified font pack (via `theme.render_pdf_with_theme`) so they
can be compared side by side against the Outfit baseline.

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```
