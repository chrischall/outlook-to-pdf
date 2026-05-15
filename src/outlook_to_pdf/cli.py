from __future__ import annotations

import sys
from pathlib import Path

import click

from .converter import convert_msg_to_pdf


def _expand_inputs(inputs: tuple[Path, ...], recursive: bool) -> list[Path]:
    """Resolve each input to a list of .msg files.

    - A file is kept as-is (whatever its extension — caller asked for it).
    - A directory is scanned for *.msg children (recursively if requested).
    - Results are de-duplicated while preserving order.
    """
    pattern = "**/*.msg" if recursive else "*.msg"
    seen: set[Path] = set()
    out: list[Path] = []
    for p in inputs:
        if p.is_dir():
            for child in sorted(p.glob(pattern)):
                if child.is_file():
                    rp = child.resolve()
                    if rp not in seen:
                        seen.add(rp)
                        out.append(child)
        else:
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                out.append(p)
    return out


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument(
    "inputs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output PDF path. Only valid with a single resolved input. Defaults to <input>.pdf.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write PDFs into this directory instead of next to each input.",
)
@click.option(
    "-r",
    "--recursive",
    is_flag=True,
    help="When an input is a directory, recurse into subdirectories looking for .msg files.",
)
@click.option(
    "--embed-attachments/--no-embed-attachments",
    default=True,
    help="Embed attachments inside the PDF (extractable from viewer's attachments panel). Default on.",
)
@click.option(
    "--extract-attachments/--no-extract-attachments",
    default=False,
    help="Also write attachments to a sidecar folder named <input>_attachments/.",
)
@click.option(
    "--allow-network",
    is_flag=True,
    help="Allow WeasyPrint to fetch http(s) URLs referenced in the email body. "
    "Off by default to avoid tracking-pixel callbacks that confirm receipt to the sender.",
)
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output.")
def cli(
    inputs: tuple[Path, ...],
    output: Path | None,
    output_dir: Path | None,
    recursive: bool,
    embed_attachments: bool,
    extract_attachments: bool,
    allow_network: bool,
    quiet: bool,
) -> None:
    """Convert Outlook .msg files to PDF.

    INPUTS may be individual .msg files, directories containing .msg files,
    or shell globs (which the shell expands before invocation).
    """
    resolved = _expand_inputs(inputs, recursive=recursive)
    if not resolved:
        raise click.UsageError(
            "no .msg files found in the given inputs"
            + (" (try -r to recurse into subdirectories)" if not recursive else "")
        )

    if output is not None and len(resolved) > 1:
        raise click.UsageError("-o/--output cannot be used with multiple inputs; use --output-dir.")

    failures = 0
    for src in resolved:
        if output is not None:
            dest = output
        elif output_dir is not None:
            dest = output_dir / (src.stem + ".pdf")
        else:
            dest = src.with_suffix(".pdf")

        attachments_dir: Path | None = None
        if extract_attachments:
            attachments_dir = dest.parent / f"{src.stem}_attachments"

        try:
            convert_msg_to_pdf(
                src,
                dest,
                embed_attachments=embed_attachments,
                extract_attachments_to=attachments_dir,
                allow_network=allow_network,
            )
        except Exception as e:
            failures += 1
            click.echo(f"error: {src}: {e}", err=True)
            continue

        if not quiet:
            click.echo(f"{src} -> {dest}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    cli()
