# outlook-to-pdf

Python CLI that converts Outlook `.msg` files to self-contained PDFs with the
original attachments embedded inside the PDF (via the `/EmbeddedFiles` name
tree). Parsing uses `extract-msg`; rendering uses `WeasyPrint`.

## Commands

```bash
uv sync                                 # Create .venv, install project + dev deps
uv run outlook-to-pdf <inputs...>       # Run the CLI
uv run pytest                           # Full test suite (no network)
uv run pytest tests/test_cli.py -v      # Subset
uv run pytest tests/test_converter.py::test_render_pdf_embeds_attachments -v
uv build                                # Build sdist + wheel via uv_build
```

System dependency (macOS): `brew install pango` (pulls in cairo, glib,
harfbuzz, gdk-pixbuf). On Linux: distro `libpango` packages — see README.

## Architecture

```
src/outlook_to_pdf/
  __init__.py    # re-exports cli.cli as `main` (package script entry point)
  cli.py         # click CLI: input expansion, output routing, error aggregation
  converter.py   # parse_message, render_html, render_pdf, convert_msg_to_pdf
tests/
  test_cli.py            # Click runner-based CLI tests (16)
  test_converter.py      # Unit + integration tests (39) — round-trips a real .msg
  fixtures/strangeDate.msg   # Real fixture borrowed from extract-msg corpus
```

Flow inside `convert_msg_to_pdf`:
1. `extract_msg.openMsg()` opens the compound file.
2. `parse_message()` walks attachments once, producing `ParsedEmail` with:
   - `attachments` (visible names — purely-inline images excluded),
   - `embedded_files` (`(name, bytes)` for `/EmbeddedFiles`),
   - `inline_resources` (`cid` → `(bytes, mime, name)` for `cid:` resolution).
3. Optional sidecar extraction via `_extract_attachments_to_disk()`.
4. `render_pdf()` renders HTML through WeasyPrint with a strict URL fetcher.

`_make_url_fetcher` allowlist: `cid:` resolves from `inline_resources`,
`data:` passes through, **everything else is blocked** (returns a 1×1 blank
PNG) unless `allow_network=True`.

## CLI surface

```
outlook-to-pdf INPUTS...
  -o/--output PATH                Single input only; default <input>.pdf
  --output-dir PATH               Write PDFs into this dir
  -r/--recursive                  Recurse into directory inputs for *.msg
  --embed-attachments / --no-embed-attachments   (default: on)
  --extract-attachments / --no-extract-attachments
                                  Also write sidecar <input>_attachments/
  --allow-network                 Permit http(s)/file/etc. fetches (off by default)
  -q/--quiet
```

`INPUTS` may mix files, directories, and shell-expanded globs. Directory
scans are non-recursive unless `-r`. Inputs are de-duplicated by resolved
path so `inbox/ inbox/a.msg` doesn't double-convert.

Per-file errors are caught and reported to stderr; the process exits `1`
only after attempting every input if any failed.

## Environment

- `HOMEBREW_PREFIX` — optional; if set, `<prefix>/lib` is prepended to
  `DYLD_FALLBACK_LIBRARY_PATH` on macOS before importing WeasyPrint.
- `DYLD_FALLBACK_LIBRARY_PATH` — auto-augmented on macOS with `/opt/homebrew/lib`
  and `/usr/local/lib` if they exist (see `_ensure_macos_native_libs` in
  `converter.py`). No user action normally required on Apple Silicon.

No API keys, no network access in normal operation.

## Testing

- Runner: `pytest` ≥ 9 (declared in `[dependency-groups].dev`).
- `pyproject.toml` sets `filterwarnings = ["error::DeprecationWarning",
  "error::PendingDeprecationWarning"]` — any deprecation warning fails the
  suite. When updating deps, watch for new warnings.
- Integration tests round-trip a zip through the PDF and verify byte
  equality via `pypdf` (dev dep).
- No network is needed; the fixture `tests/fixtures/strangeDate.msg` is real.
- Test-driven development is the convention here — add a failing test first.

## Conventions

- Python ≥ 3.12 (`.python-version` pins `3.12`; `requires-python = ">=3.12"`).
- `from __future__ import annotations` at the top of every module.
- Type hints throughout; `Protocol` used for the message-like duck type so
  unit tests don't need a real `.msg`.
- Heavy imports (`weasyprint`, `extract_msg`) are deferred into function
  bodies to keep `--help` fast and avoid loading native libs at import time.
- Attachment filenames from `.msg` are attacker-controlled — always go
  through `_sanitize_filename()` before touching disk.

<!-- pr-workflow:v3 -->
## Pull requests & release notes

Fleet policy — Conventional-Commit PR titles, labels, the auto-review /
auto-merge ladder, auto-review follow-up issues, PR timing, and release PRs —
lives in `~/.claude/CLAUDE.md`. Don't restate it here; the copies drifted.

Shared technical conventions (publishing, bundling, versioning guards,
write-verification, transport archetypes, testing traps) live in
[`chrischall/workflows`](https://github.com/chrischall/workflows):
`docs/fleet-conventions.md`, plus `README.md` for the CI pipeline contract.

## Gotchas

- **`-o/--output` is single-input only**: combining it with multiple resolved
  inputs raises `UsageError`. Use `--output-dir` for batches.
- **Network blocked by default**: tracking pixels in email bodies will be
  served a 1×1 PNG, not fetched. Pass `--allow-network` only if you trust
  the message.
- **CID resolution is case-insensitive on fallback**: exact match first,
  then lowercase scan of `inline_resources` keys.
- **Inline-vs-attachment classification**: an attachment with a `cid` whose
  `cid:<value>` literal appears in the HTML body is treated as *inline only*
  (not listed in the attachments panel). Everything else is both listed and
  embedded.
- **WeasyPrint native libs**: on macOS, importing WeasyPrint before
  `_ensure_macos_native_libs()` runs can crash with a cffi/dyld error.
  `render_pdf` calls it first; preserve that ordering if refactoring.
- **Deprecation warnings fail tests** (see `pyproject.toml`
  `filterwarnings`). Bumping `extract-msg`/`weasyprint` may surface new ones.
- **CI**: `.github/workflows/` has ci, pr-auto-review, auto-merge, claude, and
  release-please workflows. `pr-auto-review.yml` and `auto-merge.yml` are thin
  stubs that call `chrischall/workflows` reusable pipelines. A `pass` or `warn`
  auto-review verdict arms `ready-to-merge` and the PR squash-merges once CI is
  green; `warn`/`fail` also open an `auto-review-followup` issue, and only
  `fail` blocks the merge (see *Auto-review follow-up issues*).
- **Versioning**: release-please owns version bumps and tags. The version lives
  in `pyproject.toml` (`version`) and `.release-please-manifest.json`, kept in
  sync by release-please's `python` release-type. Don't bump by hand or cut
  tags — merging the release PR release-please opens does it.
