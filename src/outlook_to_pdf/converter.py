from __future__ import annotations

import datetime as dt
import os
import re
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Protocol, runtime_checkable


def _ensure_macos_native_libs() -> None:
    """WeasyPrint relies on pango/cairo/glib via cffi. On macOS these come from
    Homebrew but live outside the default dyld search path, so cffi's
    ctypes.util.find_library() can't see them. Make them visible before import.
    """
    if sys.platform != "darwin":
        return
    candidates = [
        os.environ.get("HOMEBREW_PREFIX", "") + "/lib" if os.environ.get("HOMEBREW_PREFIX") else "",
        "/opt/homebrew/lib",
        "/usr/local/lib",
    ]
    extra = [p for p in candidates if p and os.path.isdir(p)]
    if not extra:
        return
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    parts = [p for p in existing.split(":") if p]
    for p in extra:
        if p not in parts:
            parts.append(p)
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)


@runtime_checkable
class _MessageLike(Protocol):
    subject: object
    sender: object
    to: object
    cc: object
    bcc: object
    date: object
    body: object
    htmlBody: object
    attachments: object


@dataclass
class ParsedEmail:
    subject: str
    sender: str | None
    to: str | None
    cc: str | None
    bcc: str | None
    date_display: str | None
    text_body: str
    html_body: str | None
    attachments: list[str] = field(default_factory=list)
    attachments_embedded: bool = False


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return value.decode(enc)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    s = str(value).strip()
    return s or None


def _format_date(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M %Z").strip()
    return _coerce_str(value)


def _attachment_name(att: object) -> str:
    for attr in ("longFilename", "shortFilename", "displayName"):
        name = _coerce_str(getattr(att, attr, None))
        if name:
            return name
    getter = getattr(att, "getFilename", None)
    if callable(getter):
        name = _coerce_str(getter())
        if name:
            return name
    return "attachment.bin"


def parse_message(msg: _MessageLike) -> ParsedEmail:
    subject = _coerce_str(msg.subject) or "(no subject)"

    html_body_raw = msg.htmlBody
    html_body = _coerce_str(html_body_raw) if html_body_raw else None

    text_body = _coerce_str(msg.body) or ""

    attachments = [_attachment_name(a) for a in (msg.attachments or [])]

    return ParsedEmail(
        subject=subject,
        sender=_coerce_str(msg.sender),
        to=_coerce_str(msg.to),
        cc=_coerce_str(msg.cc),
        bcc=_coerce_str(msg.bcc),
        date_display=_format_date(msg.date),
        text_body=text_body,
        html_body=html_body,
        attachments=attachments,
    )


_HTML_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)


def _extract_body_inner(html: str) -> str:
    m = _HTML_BODY_RE.search(html)
    return m.group(1) if m else html


def _text_to_html(text: str) -> str:
    if not text:
        return ""
    escaped = escape(text)
    return escaped.replace("\r\n", "<br>\n").replace("\n", "<br>\n").replace("\r", "<br>\n")


_HEADER_CSS = """
  body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
         font-size: 11pt; color: #222; margin: 0; }
  .meta { border-bottom: 1px solid #ccc; padding-bottom: 8pt; margin-bottom: 12pt; }
  .meta h1 { font-size: 14pt; margin: 0 0 6pt 0; }
  .meta dl { display: grid; grid-template-columns: max-content 1fr;
             gap: 2pt 8pt; margin: 0; font-size: 9.5pt; }
  .meta dt { font-weight: 600; color: #555; }
  .meta dd { margin: 0; word-break: break-word; }
  .attachments { margin-top: 12pt; padding-top: 8pt; border-top: 1px dashed #bbb;
                 font-size: 9.5pt; }
  .attachments ul { margin: 4pt 0 0 16pt; padding: 0; }
  .body { line-height: 1.4; }
  .body pre { white-space: pre-wrap; word-wrap: break-word; }
  img { max-width: 100%; }
  table { max-width: 100%; }
"""


def render_html(parsed: ParsedEmail) -> str:
    rows: list[str] = []

    def add(label: str, value: str | None) -> None:
        if value:
            rows.append(f"  <dt>{escape(label)}</dt><dd>{escape(value)}</dd>")

    add("From", parsed.sender)
    add("To", parsed.to)
    add("Cc", parsed.cc)
    add("Bcc", parsed.bcc)
    add("Date", parsed.date_display)

    if parsed.html_body:
        body_html = _extract_body_inner(parsed.html_body)
    else:
        body_html = _text_to_html(parsed.text_body)

    attachments_html = ""
    if parsed.attachments:
        items = "\n".join(f"    <li>{escape(name)}</li>" for name in parsed.attachments)
        note = (
            " &mdash; embedded in this PDF; open the attachments panel in your "
            "PDF viewer (Preview sidebar, Acrobat paperclip) to save them out"
            if parsed.attachments_embedded
            else ""
        )
        attachments_html = (
            f'<section class="attachments">\n'
            f"  <strong>Attachments ({len(parsed.attachments)}){note}</strong>\n"
            f"  <ul>\n{items}\n  </ul>\n"
            f"</section>"
        )

    return (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="utf-8">\n'
        f"<title>{escape(parsed.subject)}</title>\n"
        f"<style>{_HEADER_CSS}</style>\n"
        "</head><body>\n"
        '<header class="meta">\n'
        f"  <h1>{escape(parsed.subject)}</h1>\n"
        "  <dl>\n"
        + "\n".join(rows)
        + "\n  </dl>\n</header>\n"
        f'<section class="body">{body_html}</section>\n'
        f"{attachments_html}\n"
        "</body></html>"
    )


def _make_cid_fetcher(cid_map: dict[str, tuple[bytes, str]]):
    """Build a WeasyPrint url_fetcher that resolves `cid:` URLs from a map.

    Falls back to WeasyPrint's default fetcher for any other URL scheme.
    """
    from weasyprint import default_url_fetcher
    from weasyprint.urls import URLFetcherResponse

    def fetch(url: str, timeout: int = 10, ssl_context=None):
        if url.startswith("cid:"):
            cid = url[4:].strip("<>").strip()
            entry = cid_map.get(cid)
            if entry is None:
                for k, v in cid_map.items():
                    if k.lower() == cid.lower():
                        entry = v
                        break
            if entry is None:
                data, mime = _BLANK_PNG, "image/png"
            else:
                data, mime = entry
                mime = mime or "application/octet-stream"
            return URLFetcherResponse(url, body=data, headers={"Content-Type": mime})
        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)

    return fetch


_BLANK_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)


def render_pdf(
    parsed: ParsedEmail,
    out_path: str | Path,
    *,
    embedded: list[tuple[str, bytes]] | None = None,
    inline_resources: dict[str, tuple[bytes, str]] | None = None,
    base_url: str | None = None,
) -> Path:
    """Render a parsed email to PDF.

    ``embedded`` — list of (name, bytes) to embed in the PDF's attachments panel.
    ``inline_resources`` — map of Content-ID → (bytes, mime) used to resolve
    `cid:` URLs in the HTML body (inline images from the original .msg).
    """
    _ensure_macos_native_libs()
    from weasyprint import HTML, Attachment

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    attachments = None
    if embedded:
        attachments = [
            Attachment(string=data, name=name, description=name)
            for name, data in embedded
        ]

    url_fetcher = _make_cid_fetcher(inline_resources) if inline_resources else None

    html = render_html(parsed)
    kwargs = {"string": html, "base_url": base_url}
    if url_fetcher is not None:
        kwargs["url_fetcher"] = url_fetcher
    HTML(**kwargs).write_pdf(str(out_path), attachments=attachments)
    return out_path


def _normalize_cid(value: object) -> str | None:
    s = _coerce_str(value)
    if not s:
        return None
    return s.strip("<>").strip() or None


def _collect_msg_resources(
    msg: _MessageLike, html_body: str | None
) -> tuple[list[tuple[str, bytes]], dict[str, tuple[bytes, str]], list[str]]:
    """Walk the .msg's attachments and split them into:
      - regular attachments to embed/list (name, bytes)
      - inline image resources keyed by CID (cid -> (bytes, mime))
      - display names for the visible Attachments section

    An attachment is treated as inline (and excluded from the visible list)
    when it has a Content-ID that is referenced by a `cid:` URL in the HTML body.
    """
    haystack = html_body or ""
    embed: list[tuple[str, bytes]] = []
    inline: dict[str, tuple[bytes, str]] = {}
    visible: list[str] = []

    for att in (msg.attachments or []):
        data = getattr(att, "data", None)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            continue
        raw = bytes(data)
        name = _attachment_name(att)
        cid = _normalize_cid(getattr(att, "cid", None) or getattr(att, "contentId", None))
        mime = _coerce_str(getattr(att, "mimetype", None)) or "application/octet-stream"

        cid_referenced = bool(cid) and (f"cid:{cid}" in haystack)
        if cid:
            inline[cid] = (raw, mime)

        if cid_referenced:
            # purely inline — don't clutter the attachment list / embedded files pile
            continue

        embed.append((name, raw))
        visible.append(name)

    return embed, inline, visible


def convert_msg_to_pdf(
    msg_path: str | Path,
    pdf_path: str | Path,
    *,
    embed_attachments: bool = True,
    extract_attachments_to: str | Path | None = None,
) -> Path:
    import extract_msg

    msg_path = Path(msg_path)
    pdf_path = Path(pdf_path)

    with extract_msg.openMsg(str(msg_path)) as msg:
        parsed = parse_message(msg)
        embed_list, inline_resources, visible_names = _collect_msg_resources(
            msg, parsed.html_body
        )

        # Override the parsed.attachments list so inline-only images don't show up
        parsed.attachments = visible_names
        parsed.attachments_embedded = bool(embed_list) and embed_attachments

        if extract_attachments_to is not None and msg.attachments:
            target = Path(extract_attachments_to)
            target.mkdir(parents=True, exist_ok=True)
            for att in msg.attachments:
                try:
                    att.save(customPath=str(target))
                except Exception:
                    pass

    return render_pdf(
        parsed,
        pdf_path,
        embedded=embed_list if embed_attachments else None,
        inline_resources=inline_resources or None,
        base_url=str(msg_path.parent),
    )
