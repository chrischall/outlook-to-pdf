from __future__ import annotations

import datetime as dt
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from outlook_to_pdf.converter import (
    ParsedEmail,
    _make_url_fetcher,
    _sanitize_filename,
    _extract_attachments_to_disk,
    convert_msg_to_pdf,
    parse_message,
    render_html,
    render_pdf,
)


@dataclass
class FakeAttachment:
    longFilename: str | None = None
    shortFilename: str | None = None
    cid: str | None = None
    mimetype: str | None = None
    data: bytes | None = None

    def getFilename(self) -> str:
        return self.longFilename or self.shortFilename or "attachment.bin"


@dataclass
class FakeMessage:
    """Quacks like extract_msg.Message — just the surface our parser uses."""

    subject: str | None = "Hello"
    sender: str | None = "Alice <alice@example.com>"
    to: str | None = "bob@example.com"
    cc: str | None = None
    bcc: str | None = None
    date: dt.datetime | str | None = dt.datetime(2026, 5, 15, 10, 30)
    body: str | None = "plain body"
    htmlBody: bytes | None = None
    attachments: list[FakeAttachment] = field(default_factory=list)


SAMPLE_MSG = Path(__file__).parent / "fixtures" / "strangeDate.msg"


# --------------------------- parse_message ---------------------------


def test_parse_extracts_basic_fields():
    parsed = parse_message(
        FakeMessage(
            subject="Quarterly Review",
            sender="Alice <alice@example.com>",
            to="bob@example.com; carol@example.com",
            cc="dave@example.com",
            body="See attached.",
        )
    )

    assert isinstance(parsed, ParsedEmail)
    assert parsed.subject == "Quarterly Review"
    assert parsed.sender == "Alice <alice@example.com>"
    assert parsed.to == "bob@example.com; carol@example.com"
    assert parsed.cc == "dave@example.com"
    assert parsed.text_body == "See attached."
    assert parsed.html_body is None
    assert parsed.attachments == []
    assert parsed.embedded_files == []
    assert parsed.inline_resources == {}


def test_parse_decodes_html_body_bytes():
    parsed = parse_message(FakeMessage(htmlBody=b"<p>hi &amp; bye</p>"))
    assert parsed.html_body == "<p>hi &amp; bye</p>"


def test_parse_records_attachment_names():
    parsed = parse_message(
        FakeMessage(attachments=[
            FakeAttachment(longFilename="report.pdf"),
            FakeAttachment(longFilename="logo.png"),
        ])
    )
    assert parsed.attachments == ["report.pdf", "logo.png"]


def test_parse_handles_empty_subject():
    parsed = parse_message(FakeMessage(subject=None, body=None, htmlBody=None))
    assert parsed.subject == "(no subject)"


def test_parse_splits_inline_vs_visible_attachments():
    html = '<p><img src="cid:logo@x"></p><img src="cid:banner@x">'
    parsed = parse_message(FakeMessage(
        htmlBody=html.encode("utf-8"),
        attachments=[
            FakeAttachment(longFilename="logo.png",   data=b"PNG1", cid="logo@x",   mimetype="image/png"),
            FakeAttachment(longFilename="banner.png", data=b"PNG2", cid="banner@x", mimetype="image/png"),
            FakeAttachment(longFilename="report.pdf", data=b"PDFBYTES"),
            FakeAttachment(longFilename="orphan.png", data=b"PNG3", cid="not-referenced@x", mimetype="image/png"),
        ],
    ))

    # logo + banner are inline-only — NOT visible
    assert parsed.attachments == ["report.pdf", "orphan.png"]
    assert sorted(n for n, _ in parsed.embedded_files) == ["orphan.png", "report.pdf"]
    # All CID attachments are available for cid: resolution
    assert set(parsed.inline_resources) == {"logo@x", "banner@x", "not-referenced@x"}
    assert parsed.inline_resources["logo@x"] == (b"PNG1", "image/png", "logo.png")


# --------------------------- render_html ---------------------------


def test_render_html_includes_headers_and_body():
    parsed = ParsedEmail(
        subject="Greetings",
        sender="Alice <alice@example.com>",
        to="bob@example.com",
        cc="carol@example.com",
        date_display="2026-05-15 10:30",
        text_body="hello world",
    )

    out = render_html(parsed)

    assert "Greetings" in out
    assert "alice@example.com" in out
    assert "bob@example.com" in out
    assert "carol@example.com" in out
    assert "2026-05-15 10:30" in out
    assert "hello world" in out


def test_render_html_escapes_text_body_special_chars():
    out = render_html(ParsedEmail(text_body="<script>alert('xss')</script>"))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_html_preserves_text_body_linebreaks():
    out = render_html(ParsedEmail(text_body="line one\nline two\r\nline three"))
    assert out.count("<br") >= 2


def test_render_html_prefers_html_body_when_present():
    out = render_html(ParsedEmail(
        text_body="ignored plain",
        html_body="<div data-test='kept'>Rich content</div>",
    ))
    assert "Rich content" in out
    assert "data-test" in out
    assert "ignored plain" not in out


def test_render_html_lists_attachments():
    out = render_html(ParsedEmail(attachments=["report.pdf", "logo.png"]))
    assert "report.pdf" in out
    assert "logo.png" in out


def test_render_html_omits_attachments_section_when_none():
    out = render_html(ParsedEmail())
    assert "Attachments" not in out


# --------------------------- render_pdf + embedded files ---------------------------


def _pdf_embedded_files(pdf_path: Path) -> dict[str, bytes]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    result: dict[str, bytes] = {}
    for name, content in (reader.attachments or {}).items():
        result[name] = content[0] if isinstance(content, list) else content
    return result


def test_render_pdf_without_attachments_produces_pdf(tmp_path):
    out = render_pdf(ParsedEmail(text_body="hi"), tmp_path / "no-attach.pdf")
    assert out.read_bytes()[:4] == b"%PDF"
    assert _pdf_embedded_files(out) == {}


def test_render_pdf_embeds_attachments(tmp_path):
    payload = bytes(range(64))
    parsed = ParsedEmail(
        attachments=["hello.txt", "data.bin"],
        embedded_files=[("hello.txt", b"hello world"), ("data.bin", payload)],
    )
    out = render_pdf(parsed, tmp_path / "with-attach.pdf")

    files = _pdf_embedded_files(out)
    assert files == {"hello.txt": b"hello world", "data.bin": payload}


def test_render_pdf_handles_binary_zip_payload(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "inside the zip")
    zip_bytes = buf.getvalue()

    parsed = ParsedEmail(
        attachments=["files.zip"],
        embedded_files=[("files.zip", zip_bytes)],
    )
    out = render_pdf(parsed, tmp_path / "zip-attach.pdf")

    files = _pdf_embedded_files(out)
    assert files["files.zip"] == zip_bytes
    with zipfile.ZipFile(io.BytesIO(files["files.zip"])) as zf:
        assert zf.read("readme.txt") == b"inside the zip"


def test_render_pdf_resolves_cid_inline_image(tmp_path):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="PNG")
    red_png = buf.getvalue()

    parsed = ParsedEmail(
        subject="inline-img",
        html_body='<html><body><p>Look:</p><img src="cid:pic1@example"></body></html>',
        inline_resources={"pic1@example": (red_png, "image/png", "pic1.png")},
    )

    out = render_pdf(parsed, tmp_path / "inline.pdf")
    data = out.read_bytes()
    assert data[:4] == b"%PDF"
    assert b"/Image" in data or b"/XObject" in data


def test_render_pdf_missing_cid_does_not_crash(tmp_path):
    parsed = ParsedEmail(
        html_body='<html><body><img src="cid:does-not-exist"></body></html>',
        inline_resources={"other": (b"", "image/png", "other.png")},
    )
    out = render_pdf(parsed, tmp_path / "missing.pdf")
    assert out.read_bytes()[:4] == b"%PDF"


# --------------------------- URL fetcher policy (privacy/security) ---------------------------


def test_url_fetcher_blocks_http_by_default():
    fetch = _make_url_fetcher({}, allow_network=False)
    resp = fetch("http://tracker.example/pixel.png")
    body = resp.read()
    assert body.startswith(b"\x89PNG\r\n\x1a\n")  # blank PNG, no network call


def test_url_fetcher_blocks_https_by_default():
    fetch = _make_url_fetcher({}, allow_network=False)
    resp = fetch("https://attacker.example/track.gif")
    assert resp.read().startswith(b"\x89PNG\r\n\x1a\n")


def test_url_fetcher_resolves_cid_even_with_network_blocked():
    fetch = _make_url_fetcher(
        {"abc@x": (b"REAL-IMAGE-BYTES", "image/png", "pic.png")},
        allow_network=False,
    )
    resp = fetch("cid:abc@x")
    assert resp.read() == b"REAL-IMAGE-BYTES"


def test_url_fetcher_blocked_url_does_not_hit_network(monkeypatch):
    # default_url_fetcher would do real I/O — make sure we never reach it
    import weasyprint
    called = []

    def boom(*a, **kw):
        called.append((a, kw))
        raise AssertionError("default_url_fetcher should not be called for a blocked URL")

    monkeypatch.setattr(weasyprint, "default_url_fetcher", boom)
    fetch = _make_url_fetcher({}, allow_network=False)
    fetch("http://tracker.example/pixel.png")
    assert called == []


# --------------------------- filename sanitization ---------------------------


@pytest.mark.parametrize("raw, expected", [
    ("report.pdf", "report.pdf"),
    ("../../etc/passwd", "passwd"),
    ("..\\..\\windows\\system32\\evil.dll", "evil.dll"),
    ("a/b/c.txt", "c.txt"),
    ("with\x00null.bin", "with_null.bin"),
    ("with\nnewline.bin", "with_newline.bin"),
    ("C:nasty.txt", "C_nasty.txt"),
    ("", "attachment.bin"),
    ("   ", "attachment.bin"),
    (".hidden", "hidden"),
])
def test_sanitize_filename(raw, expected):
    assert _sanitize_filename(raw) == expected


def test_extract_attachments_to_disk_uses_safe_names(tmp_path):
    parsed = ParsedEmail(
        embedded_files=[
            ("../../etc/passwd", b"good"),
            ("normal.txt", b"hello"),
        ],
        inline_resources={
            "cid1": (b"img", "image/png", "../escape/logo.png"),
        },
    )
    target = tmp_path / "attach"
    _extract_attachments_to_disk(parsed, target)

    written = sorted(p.name for p in target.iterdir())
    assert written == ["logo.png", "normal.txt", "passwd"]
    assert (target / "passwd").read_bytes() == b"good"
    # And nothing escaped target/
    assert not (tmp_path / "passwd").exists()
    assert not (tmp_path / "escape").exists()


def test_extract_attachments_to_disk_avoids_collisions(tmp_path):
    parsed = ParsedEmail(
        embedded_files=[
            ("dup.txt", b"first"),
            ("dup.txt", b"second"),
            ("dup.txt", b"third"),
        ],
    )
    target = tmp_path / "out"
    _extract_attachments_to_disk(parsed, target)

    names = sorted(p.name for p in target.iterdir())
    assert names == ["dup.txt", "dup_1.txt", "dup_2.txt"]
    contents = {p.name: p.read_bytes() for p in target.iterdir()}
    assert contents["dup.txt"] == b"first"
    assert {contents["dup_1.txt"], contents["dup_2.txt"]} == {b"second", b"third"}


# --------------------------- end-to-end integration ---------------------------


@pytest.mark.skipif(not SAMPLE_MSG.exists(), reason="sample .msg fixture missing")
def test_convert_real_msg_produces_valid_pdf(tmp_path):
    out = tmp_path / "out.pdf"
    result = convert_msg_to_pdf(SAMPLE_MSG, out)
    assert result == out
    assert out.stat().st_size > 1000
    assert out.read_bytes()[:4] == b"%PDF"
