from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from outlook_to_pdf.cli import cli


SAMPLE_MSG = Path(__file__).parent / "fixtures" / "strangeDate.msg"


def test_cli_requires_input_path():
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code != 0


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_converts_sample(tmp_path):
    out = tmp_path / "out.pdf"
    runner = CliRunner()
    result = runner.invoke(cli, [str(SAMPLE_MSG), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.read_bytes()[:4] == b"%PDF"


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_defaults_output_alongside_input(tmp_path):
    src = tmp_path / "sample.msg"
    src.write_bytes(SAMPLE_MSG.read_bytes())
    runner = CliRunner()
    result = runner.invoke(cli, [str(src)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "sample.pdf").exists()


def test_cli_errors_on_missing_input(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, [str(tmp_path / "nope.msg")])
    assert result.exit_code != 0
