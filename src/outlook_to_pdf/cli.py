from __future__ import annotations

import sys
from pathlib import Path

import click

from .converter import convert_msg_to_pdf


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument(
    "inputs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output PDF path. Only valid with a single input. Defaults to <input>.pdf.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write PDFs into this directory instead of next to each input.",
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
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output.")
def cli(
    inputs: tuple[Path, ...],
    output: Path | None,
    output_dir: Path | None,
    embed_attachments: bool,
    extract_attachments: bool,
    quiet: bool,
) -> None:
    """Convert Outlook .msg files to PDF."""
    if output is not None and len(inputs) > 1:
        raise click.UsageError("-o/--output cannot be used with multiple inputs; use --output-dir.")

    failures = 0
    for src in inputs:
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
