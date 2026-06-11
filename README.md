# outlook-to-pdf

[![CI](https://github.com/chrischall/outlook-to-pdf/actions/workflows/ci.yml/badge.svg)](https://github.com/chrischall/outlook-to-pdf/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A small Python CLI that converts Outlook `.msg` files into self-contained PDFs,
with the original attachments **embedded inside the PDF** so the recipient can
extract them from any modern viewer.

Built on [`extract-msg`](https://github.com/TeamMsgExtractor/msg-extractor) (parses
the Outlook compound-file format without needing Windows or Outlook installed)
and [`WeasyPrint`](https://weasyprint.org) (renders the email's HTML body to PDF).

## Features

- Parses Outlook `.msg` files cross-platform — no Outlook, no Windows required
- Preserves the original HTML body when present, falls back to the plain-text body
- Resolves inline `cid:` image references so embedded images actually appear
- Embeds attachments inside the PDF (extractable from Preview, Acrobat, Foxit, Firefox, …)
- **Blocks network fetches by default** so tracking pixels in the email body
  can't phone home; pass `--allow-network` if you really need remote assets
- Sanitizes attachment filenames before writing to disk (no path traversal)
- Optional sidecar folder for attachments
- Batch-converts multiple files in one invocation (files, directories, `-r`)

## Requirements

- **Python ≥ 3.12**
- **macOS / Linux** native libraries for WeasyPrint (pango, cairo, glib, harfbuzz)
- [**uv**](https://github.com/astral-sh/uv) — recommended package manager

### Install system dependencies

**macOS (Homebrew):**
```sh
brew install pango
```
That pulls in cairo, glib, harfbuzz, and gdk-pixbuf as transitive dependencies.
On macOS the CLI auto-detects `/opt/homebrew/lib` so no `DYLD_*` env vars are needed.

**Debian / Ubuntu:**
```sh
sudo apt install libpango-1.0-0 libpangoft2-1.0-0
```

**Fedora / RHEL:**
```sh
sudo dnf install pango
```

## Install

```sh
git clone https://github.com/chrischall/outlook-to-pdf.git
cd outlook-to-pdf
uv sync
```

`uv sync` creates a `.venv`, installs the project, and exposes the `outlook-to-pdf`
script in `.venv/bin/`.

## Run

```sh
# Single file → writes email.pdf next to email.msg
uv run outlook-to-pdf email.msg

# Custom output path
uv run outlook-to-pdf email.msg -o /tmp/output.pdf

# Multiple files via shell glob (shell expands *.msg before invocation)
uv run outlook-to-pdf inbox/*.msg --output-dir ./pdfs

# A whole directory of .msg files (non-recursive)
uv run outlook-to-pdf ./inbox --output-dir ./pdfs

# Recurse into subdirectories
uv run outlook-to-pdf -r ./archive --output-dir ./pdfs

# Mix and match — file, directory, glob — all in one invocation
uv run outlook-to-pdf urgent.msg ./inbox -r ./old-archives

# Also write attachments to a sidecar folder (in addition to embedding)
uv run outlook-to-pdf email.msg --extract-attachments

# Skip embedding attachments (just list them by name)
uv run outlook-to-pdf email.msg --no-embed-attachments

# Allow the renderer to fetch remote images/CSS (off by default for privacy)
uv run outlook-to-pdf email.msg --allow-network
```

### Input expansion

| Input form | What happens |
| --- | --- |
| `foo.msg` | Converted as-is |
| `*.msg` | Shell expands the glob first; each match is converted |
| `inbox/` (directory) | Scans for `*.msg` children (non-recursive by default) |
| `inbox/ -r` | Walks the directory tree, picks up every `.msg` it finds |
| Duplicates (e.g. `inbox/ inbox/a.msg`) | De-duplicated by resolved path; each file converted once |

Run `uv run outlook-to-pdf --help` for the full option list.

After `uv sync` you can also call it directly without the `uv run` prefix:
```sh
./.venv/bin/outlook-to-pdf email.msg
```
or activate the venv (`source .venv/bin/activate`) and call `outlook-to-pdf` directly.

## How embedded attachments work

PDF supports embedded files (`/EmbeddedFiles` name tree) — any binary type
(zip, docx, xlsx, png, even nested PDFs) goes in as raw bytes and comes out
byte-identical. Viewers expose them through an attachments panel:

| Viewer | Where to find embedded files |
| --- | --- |
| **Preview.app** (macOS) | Markup sidebar; drag out or *File → Export* |
| **Adobe Acrobat / Reader** | Paperclip icon on the left; right-click → *Save Attachment* |
| **Foxit, PDF Expert** | Attachments panel; drag-out or save-as |
| **Firefox** | Attachments panel (built-in PDF viewer) |
| **Chrome** | Not exposed in the UI — use `pdfdetach` instead |
| CLI fallback | `pdfdetach -saveall -o out/ file.pdf` (from `poppler`) |

Once extracted to disk, an embedded `.zip` opens in Archive Utility, a `.docx`
in Word, etc. — they're just normal files.

## Development

```sh
# Run the test suite (18 tests, no network required)
uv run pytest

# Run a single test
uv run pytest tests/test_converter.py::test_render_pdf_embeds_attachments -v
```

The integration tests use a real `.msg` fixture in `tests/fixtures/strangeDate.msg`
borrowed from the upstream `extract-msg` test corpus. The attachment-embedding
tests round-trip a zip through the PDF and verify byte-equality.

## Project layout

```
src/outlook_to_pdf/
  converter.py   # parse_message, render_html, render_pdf, convert_msg_to_pdf
  cli.py         # click-based CLI entrypoint
  __init__.py
tests/
  test_converter.py
  test_cli.py
  fixtures/strangeDate.msg
```
