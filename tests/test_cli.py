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


def _seed_msg_tree(tmp_path: Path) -> Path:
    """Create a small tree of .msg files for batch tests."""
    src = SAMPLE_MSG.read_bytes()
    (tmp_path / "a.msg").write_bytes(src)
    (tmp_path / "b.msg").write_bytes(src)
    (tmp_path / "not-a-msg.txt").write_text("ignore me")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.msg").write_bytes(src)
    return tmp_path


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_accepts_multiple_files(tmp_path):
    root = _seed_msg_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, [str(root / "a.msg"), str(root / "b.msg")])
    assert result.exit_code == 0, result.output
    assert (root / "a.pdf").exists()
    assert (root / "b.pdf").exists()


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_accepts_directory_non_recursive(tmp_path):
    root = _seed_msg_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, [str(root)])
    assert result.exit_code == 0, result.output
    assert (root / "a.pdf").exists()
    assert (root / "b.pdf").exists()
    # non-recursive shouldn't descend into sub/
    assert not (root / "sub" / "c.pdf").exists()


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_recursive_picks_up_nested_files(tmp_path):
    root = _seed_msg_tree(tmp_path)
    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(cli, ["-r", str(root), "--output-dir", str(out)])
    assert result.exit_code == 0, result.output
    pdfs = sorted(p.name for p in out.glob("*.pdf"))
    assert pdfs == ["a.pdf", "b.pdf", "c.pdf"]


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_deduplicates_overlapping_inputs(tmp_path):
    root = _seed_msg_tree(tmp_path)
    runner = CliRunner()
    # passing both the dir AND a file from inside it should not double-convert
    result = runner.invoke(cli, [str(root), str(root / "a.msg"), "--output-dir", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if "->" in ln]
    # exactly 2 conversions (a, b) — not 3 (a, b, a)
    assert len(lines) == 2


def test_cli_errors_when_directory_has_no_msg(tmp_path):
    (tmp_path / "readme.txt").write_text("not a msg")
    runner = CliRunner()
    result = runner.invoke(cli, [str(tmp_path)])
    assert result.exit_code != 0
    assert "no .msg files" in result.output.lower() or "no .msg files" in (result.stderr or "").lower()
