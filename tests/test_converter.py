from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pytest

from outlook_to_pdf.converter import (
    ParsedEmail,
    parse_message,
    render_html,
    render_pdf,
    convert_msg_to_pdf,
)


@dataclass
class FakeAttachment:
    longFilename: str | None = None
    shortFilename: str | None = None

    def getFilename(self) -> str:
        return self.longFilename or self.shortFilename or "attachment.bin"


class FakeMessage:
    """Quacks like extract_msg.Message — just the surface our parser uses."""

    def __init__(
        self,
        *,
        subject: str | None = "Hello",
        sender: str | None = "Alice <alice@example.com>",
        to: str | None = "bob@example.com",
        cc: str | None = None,
        bcc: str | None = None,
        date: dt.datetime | str | None = dt.datetime(2026, 5, 15, 10, 30),
        body: str | None = "plain body",
        htmlBody: bytes | None = None,
        attachments: list[FakeAttachment] | None = None,
    ):
        self.subject = subject
        self.sender = sender
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.date = date
        self.body = body
        self.htmlBody = htmlBody
        self.attachments = attachments or []


def test_parse_extracts_basic_fields():
    msg = FakeMessage(
        subject="Quarterly Review",
        sender="Alice <alice@example.com>",
        to="bob@example.com; carol@example.com",
        cc="dave@example.com",
        body="See attached.",
    )

    parsed = parse_message(msg)

    assert isinstance(parsed, ParsedEmail)
    assert parsed.subject == "Quarterly Review"
    assert parsed.sender == "Alice <alice@example.com>"
    assert parsed.to == "bob@example.com; carol@example.com"
    assert parsed.cc == "dave@example.com"
    assert parsed.text_body == "See attached."
    assert parsed.html_body is None
    assert parsed.attachments == []


def test_parse_decodes_html_body_bytes():
    html = "<p>hi &amp; bye</p>".encode("utf-8")
    parsed = parse_message(FakeMessage(htmlBody=html))
    assert parsed.html_body is not None
    assert "<p>hi &amp; bye</p>" in parsed.html_body


def test_parse_records_attachment_names():
    msg = FakeMessage(
        attachments=[FakeAttachment(longFilename="report.pdf"), FakeAttachment(longFilename="logo.png")]
    )
    parsed = parse_message(msg)
    assert parsed.attachments == ["report.pdf", "logo.png"]


def test_parse_handles_empty_subject():
    parsed = parse_message(FakeMessage(subject=None, body=None, htmlBody=None))
    assert parsed.subject == "(no subject)"


def test_render_html_includes_headers_and_body():
    parsed = ParsedEmail(
        subject="Greetings",
        sender="Alice <alice@example.com>",
        to="bob@example.com",
        cc="carol@example.com",
        bcc=None,
        date_display="2026-05-15 10:30",
        text_body="hello world",
        html_body=None,
        attachments=[],
    )

    out = render_html(parsed)

    assert "Greetings" in out
    assert "Alice &lt;alice@example.com&gt;" in out or "alice@example.com" in out
    assert "bob@example.com" in out
    assert "carol@example.com" in out
    assert "2026-05-15 10:30" in out
    assert "hello world" in out


def test_render_html_escapes_text_body_special_chars():
    parsed = ParsedEmail(
        subject="x",
        sender=None,
        to=None,
        cc=None,
        bcc=None,
        date_display=None,
        text_body="<script>alert('xss')</script>",
        html_body=None,
        attachments=[],
    )
    out = render_html(parsed)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_html_preserves_text_body_linebreaks():
    parsed = ParsedEmail(
        subject="x",
        sender=None,
        to=None,
        cc=None,
        bcc=None,
        date_display=None,
        text_body="line one\nline two\r\nline three",
        html_body=None,
        attachments=[],
    )
    out = render_html(parsed)
    assert out.count("<br") >= 2


def test_render_html_prefers_html_body_when_present():
    parsed = ParsedEmail(
        subject="x",
        sender=None,
        to=None,
        cc=None,
        bcc=None,
        date_display=None,
        text_body="ignored plain",
        html_body="<div data-test='kept'>Rich content</div>",
        attachments=[],
    )
    out = render_html(parsed)
    assert "Rich content" in out
    assert "data-test" in out
    assert "ignored plain" not in out


def test_render_html_lists_attachments():
    parsed = ParsedEmail(
        subject="x",
        sender=None,
        to=None,
        cc=None,
        bcc=None,
        date_display=None,
        text_body="",
        html_body=None,
        attachments=["report.pdf", "logo.png"],
    )
    out = render_html(parsed)
    assert "report.pdf" in out
    assert "logo.png" in out


def test_render_html_omits_attachments_section_when_none():
    parsed = ParsedEmail(
        subject="x",
        sender=None,
        to=None,
        cc=None,
        bcc=None,
        date_display=None,
        text_body="",
        html_body=None,
        attachments=[],
    )
    out = render_html(parsed)
    assert "Attachments" not in out


SAMPLE_MSG = Path(__file__).parent / "fixtures" / "strangeDate.msg"


def _parsed_stub(**overrides) -> ParsedEmail:
    defaults = dict(
        subject="x",
        sender=None,
        to=None,
        cc=None,
        bcc=None,
        date_display=None,
        text_body="hi",
        html_body=None,
        attachments=[],
    )
    defaults.update(overrides)
    return ParsedEmail(**defaults)


def _pdf_embedded_files(pdf_path: Path) -> dict[str, bytes]:
    """Read the /EmbeddedFiles name tree out of a PDF, returning {name: bytes}."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    result: dict[str, bytes] = {}
    for name, content in (reader.attachments or {}).items():
        # pypdf returns a list of bytes (one per matching attachment)
        result[name] = content[0] if isinstance(content, list) else content
    return result


def test_render_pdf_without_attachments_produces_pdf(tmp_path):
    out = render_pdf(_parsed_stub(), tmp_path / "no-attach.pdf")
    data = out.read_bytes()
    assert data[:4] == b"%PDF"
    assert _pdf_embedded_files(out) == {}


def test_render_pdf_embeds_attachments(tmp_path):
    parsed = _parsed_stub(
        attachments=["hello.txt", "data.bin"],
        attachments_embedded=True,
    )
    payload = bytes(range(64))
    embedded = [
        ("hello.txt", b"hello world"),
        ("data.bin", payload),
    ]
    out = render_pdf(parsed, tmp_path / "with-attach.pdf", embedded=embedded)

    files = _pdf_embedded_files(out)
    assert set(files) == {"hello.txt", "data.bin"}
    assert files["hello.txt"] == b"hello world"
    assert files["data.bin"] == payload


def test_render_pdf_handles_binary_zip_payload(tmp_path):
    import io, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "inside the zip")
    zip_bytes = buf.getvalue()
    assert zip_bytes[:2] == b"PK"

    out = render_pdf(
        _parsed_stub(attachments=["files.zip"], attachments_embedded=True),
        tmp_path / "zip-attach.pdf",
        embedded=[("files.zip", zip_bytes)],
    )

    files = _pdf_embedded_files(out)
    assert "files.zip" in files
    # Extracted bytes should round-trip back to a valid zip
    assert files["files.zip"] == zip_bytes
    with zipfile.ZipFile(io.BytesIO(files["files.zip"])) as zf:
        assert zf.read("readme.txt") == b"inside the zip"


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_convert_real_msg_produces_valid_pdf(tmp_path):
    out = tmp_path / "out.pdf"
    result = convert_msg_to_pdf(SAMPLE_MSG, out)
    assert result == out
    assert out.exists()
    assert out.stat().st_size > 1000
    with out.open("rb") as f:
        assert f.read(4) == b"%PDF"
