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
Fonts (the Outfit family) are bundled and installed automatically so
rendering is identical regardless of which machine runs it.

---

## Tools

| Tool | Description |
|------|-------------|
| `create_agenda(date, meetings?, daily_schedule?, tasks?, render?, include_base64?)` | Create a fresh agenda for `date` (`YYYY-MM-DD`). Errors if one already exists. Optional args run the rest of the setup end-to-end in the same call: refresh calendar blocks, add every meeting in `meetings`, fill `daily_schedule`, append `tasks`, and render to PDF if `render` is true. |
| `adjust_dates(date)` | Refresh every calendar header/footer block and the "next 4 weeks" grid for an existing agenda. |
| `add_meeting(date, title)` | Fill the first blank meeting slot, or clone and append a new meeting page (calendar header + title + ruled notes table), always as a single page. A title too long for one line is cut off at the end, never wrapped. |
| `add_daily_schedule(date, entries)` | Fill specific hour slots (`8am`..`6pm`) in the page-1 daily schedule. Each entry: `{hour, text}`. Text that doesn't fit is cut off at the end, never wrapped. |
| `add_tasks(date, tasks)` | Append tasks to the page-1 to-do list, filling empty rows top-down (18-row capacity). Each task: `{text, due}`. Long task text shrinks down to 9pt before wrapping across multiple lines. |
| `render_pdf(date)` | Render the working docx to PDF via headless LibreOffice. |

Agendas are stored one docx (and rendered PDF) per date under
`~/.magenda/agendas/`.

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

The bundled Outfit font family (`assets/fonts/`) ships pre-generated and is
installed into the user's font directory automatically before the first
render — see `src/magenda/font_setup.py`. To regenerate those font files
from the canonical Google Fonts variable font: `python scripts/build_fonts.py`.

---

## Manual testing

`scripts/manual_test.py` builds a realistic agenda for **today** end-to-end
(meetings, schedule, tasks) and renders it to PDF for visual inspection:

```bash
python scripts/manual_test.py
```

It prints the resulting PDF path — open it and check the layout against
`assets/template.docx` converted to PDF.

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```
