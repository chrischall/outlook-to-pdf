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


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_o_with_multiple_inputs_is_usage_error(tmp_path):
    root = _seed_msg_tree(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, [str(root / "a.msg"), str(root / "b.msg"), "-o", str(tmp_path / "x.pdf")])
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "-o" in combined.lower() or "--output" in combined.lower()


def test_cli_malformed_msg_fails_gracefully(tmp_path):
    bad = tmp_path / "fake.msg"
    bad.write_text("this is not a CFB compound file")
    runner = CliRunner()
    result = runner.invoke(cli, [str(bad)])
    assert result.exit_code != 0
    assert "error:" in (result.output + (result.stderr or "")).lower()
    # Should not have written a half-baked PDF
    assert not (tmp_path / "fake.pdf").exists()


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_extract_attachments_creates_sidecar(tmp_path, monkeypatch):
    """Verify --extract-attachments propagates and writes a sidecar dir."""
    src = tmp_path / "with-att.msg"
    src.write_bytes(SAMPLE_MSG.read_bytes())

    captured: dict = {}
    import outlook_to_pdf.cli as cli_mod
    real = cli_mod.convert_msg_to_pdf

    def spy(msg_path, pdf_path, **kw):
        captured.update(kw)
        return real(msg_path, pdf_path, **kw)

    monkeypatch.setattr(cli_mod, "convert_msg_to_pdf", spy)

    runner = CliRunner()
    result = runner.invoke(cli, [str(src), "--extract-attachments"])
    assert result.exit_code == 0, result.output
    assert captured.get("extract_attachments_to") == tmp_path / "with-att_attachments"


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_no_embed_flag_propagates(tmp_path, monkeypatch):
    src = tmp_path / "x.msg"
    src.write_bytes(SAMPLE_MSG.read_bytes())

    captured: dict = {}
    import outlook_to_pdf.cli as cli_mod
    real = cli_mod.convert_msg_to_pdf

    def spy(msg_path, pdf_path, **kw):
        captured.update(kw)
        return real(msg_path, pdf_path, **kw)

    monkeypatch.setattr(cli_mod, "convert_msg_to_pdf", spy)

    runner = CliRunner()
    result = runner.invoke(cli, [str(src), "--no-embed-attachments"])
    assert result.exit_code == 0, result.output
    assert captured["embed_attachments"] is False


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_allow_network_flag_propagates(tmp_path, monkeypatch):
    src = tmp_path / "x.msg"
    src.write_bytes(SAMPLE_MSG.read_bytes())

    captured: dict = {}
    import outlook_to_pdf.cli as cli_mod
    real = cli_mod.convert_msg_to_pdf

    def spy(msg_path, pdf_path, **kw):
        captured.update(kw)
        return real(msg_path, pdf_path, **kw)

    monkeypatch.setattr(cli_mod, "convert_msg_to_pdf", spy)

    runner = CliRunner()
    result = runner.invoke(cli, [str(src), "--allow-network"])
    assert result.exit_code == 0, result.output
    assert captured["allow_network"] is True

    # And the default is False
    captured.clear()
    result = runner.invoke(cli, [str(src)])
    assert result.exit_code == 0
    assert captured["allow_network"] is False


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_continues_after_one_failure_and_exits_nonzero(tmp_path):
    """One bad file shouldn't stop the others — but the exit code must reflect failure."""
    good = tmp_path / "good.msg"
    good.write_bytes(SAMPLE_MSG.read_bytes())
    bad = tmp_path / "bad.msg"
    bad.write_text("not a real msg")

    runner = CliRunner()
    result = runner.invoke(cli, [str(good), str(bad)])
    assert result.exit_code != 0
    assert (tmp_path / "good.pdf").exists()  # the good one still converted
    assert not (tmp_path / "bad.pdf").exists()


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_cli_quiet_suppresses_progress(tmp_path):
    src = tmp_path / "x.msg"
    src.write_bytes(SAMPLE_MSG.read_bytes())
    runner = CliRunner()
    result = runner.invoke(cli, [str(src), "-q"])
    assert result.exit_code == 0
    assert "->" not in result.output
