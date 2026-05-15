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
    subject: str = "(no subject)"
    sender: str | None = None
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    date_display: str | None = None
    text_body: str = ""
    html_body: str | None = None
    # Visible attachment names (excludes purely inline images).
    attachments: list[str] = field(default_factory=list)
    # (name, bytes) for each visible attachment that has data.
    embedded_files: list[tuple[str, bytes]] = field(default_factory=list)
    # CID -> (bytes, mime, original_name) for inline-image resolution + sidecar.
    inline_resources: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)
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


def _normalize_cid(value: object) -> str | None:
    s = _coerce_str(value)
    if not s:
        return None
    return s.strip("<>").strip() or None


def parse_message(msg: _MessageLike) -> ParsedEmail:
    """Read a Message-like object into a ParsedEmail.

    Walks the attachment list exactly once, populating:
      - ``attachments`` — visible names (purely inline images are filtered out)
      - ``embedded_files`` — (name, bytes) for PDF /EmbeddedFiles
      - ``inline_resources`` — CID -> (bytes, mime, name) for cid: URL resolution
    """
    html_body = _coerce_str(msg.htmlBody) if msg.htmlBody else None
    haystack = html_body or ""

    attachments: list[str] = []
    embedded: list[tuple[str, bytes]] = []
    inline: dict[str, tuple[bytes, str, str]] = {}

    for att in (msg.attachments or []):
        name = _attachment_name(att)
        cid = _normalize_cid(getattr(att, "cid", None) or getattr(att, "contentId", None))
        mime = _coerce_str(getattr(att, "mimetype", None)) or "application/octet-stream"
        raw_data = getattr(att, "data", None)
        raw = bytes(raw_data) if isinstance(raw_data, (bytes, bytearray, memoryview)) else None

        if cid and raw is not None:
            inline[cid] = (raw, mime, name)

        # Purely inline images live in inline_resources only — no double-listing.
        if cid and f"cid:{cid}" in haystack:
            continue

        attachments.append(name)
        if raw is not None:
            embedded.append((name, raw))

    return ParsedEmail(
        subject=_coerce_str(msg.subject) or "(no subject)",
        sender=_coerce_str(msg.sender),
        to=_coerce_str(msg.to),
        cc=_coerce_str(msg.cc),
        bcc=_coerce_str(msg.bcc),
        date_display=_format_date(msg.date),
        text_body=_coerce_str(msg.body) or "",
        html_body=html_body,
        attachments=attachments,
        embedded_files=embedded,
        inline_resources=inline,
    )


_HTML_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)


def _extract_body_inner(html: str) -> str:
    m = _HTML_BODY_RE.search(html)
    return m.group(1) if m else html


def _text_to_html(text: str) -> str:
    if not text:
        return ""
    return escape(text).replace("\r\n", "<br>\n").replace("\n", "<br>\n").replace("\r", "<br>\n")


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


# 1x1 transparent PNG used as a stand-in when a cid: lookup misses or a network
# URL is blocked. Keeps layout from collapsing without leaking any data.
_BLANK_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)


def _make_url_fetcher(
    inline_resources: dict[str, tuple[bytes, str, str]],
    *,
    allow_network: bool,
):
    """Build a WeasyPrint url_fetcher with two policies baked in:

    1. ``cid:`` URLs resolve from ``inline_resources`` — never the network.
    2. ``http(s)``/``ftp`` URLs are blocked unless ``allow_network`` is True.
       Blocking is the default because email bodies routinely contain tracking
       pixels; fetching them on render would leak the recipient's IP and
       confirm-of-receipt to the sender.

    ``data:`` and ``file:`` URLs fall through to WeasyPrint's default fetcher.
    """
    from weasyprint import default_url_fetcher
    from weasyprint.urls import URLFetcherResponse

    def _blank_png_response(url: str) -> "URLFetcherResponse":
        return URLFetcherResponse(url, body=_BLANK_PNG, headers={"Content-Type": "image/png"})

    def fetch(url: str, timeout: int = 10, ssl_context=None):
        if url.startswith("cid:"):
            cid = url[4:].strip("<>").strip()
            entry = inline_resources.get(cid)
            if entry is None:
                lc = cid.lower()
                for k, v in inline_resources.items():
                    if k.lower() == lc:
                        entry = v
                        break
            if entry is None:
                return _blank_png_response(url)
            data, mime, _name = entry
            return URLFetcherResponse(url, body=data, headers={"Content-Type": mime or "application/octet-stream"})

        if url.startswith(("http://", "https://", "ftp://", "ftps://")) and not allow_network:
            return _blank_png_response(url)

        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)

    return fetch


def render_pdf(
    parsed: ParsedEmail,
    out_path: str | Path,
    *,
    base_url: str | None = None,
    embed_attachments: bool = True,
    allow_network: bool = False,
) -> Path:
    """Render a parsed email to PDF.

    Reads embedded attachments and inline image resources from ``parsed``.
    Network fetches are blocked unless ``allow_network`` is set.
    """
    _ensure_macos_native_libs()
    from weasyprint import HTML, Attachment

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    weasy_attachments = None
    if embed_attachments and parsed.embedded_files:
        weasy_attachments = [
            Attachment(string=data, name=name, description=name)
            for name, data in parsed.embedded_files
        ]
        parsed.attachments_embedded = True
    else:
        parsed.attachments_embedded = False

    fetcher = _make_url_fetcher(parsed.inline_resources, allow_network=allow_network)
    html = render_html(parsed)
    HTML(string=html, base_url=base_url, url_fetcher=fetcher).write_pdf(
        str(out_path), attachments=weasy_attachments
    )
    return out_path


_UNSAFE_FILENAME_RE = re.compile(r"[\x00-\x1f/\\:]")


def _sanitize_filename(name: str) -> str:
    """Reduce ``name`` to a safe basename suitable for writing to disk.

    Strips directory components, control chars, and platform-specific path
    separators / drive-letter colons. The attachment filenames in a .msg are
    attacker-controlled, so this guards the sidecar extraction path against
    traversal (`../../etc/passwd`) and NUL-byte tricks.
    """
    # Normalize Windows separators on non-Windows hosts so we still split.
    normalized = name.replace("\\", "/")
    base = os.path.basename(normalized).strip().lstrip(".")
    safe = _UNSAFE_FILENAME_RE.sub("_", base)
    return safe or "attachment.bin"


def _extract_attachments_to_disk(parsed: ParsedEmail, target: str | Path) -> None:
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()

    def _write(name: str, data: bytes) -> None:
        safe = _sanitize_filename(name)
        # collision-avoidance: if name already used, prepend an index
        candidate, n = safe, 1
        while candidate in used:
            stem, dot, ext = safe.partition(".")
            candidate = f"{stem}_{n}{dot}{ext}" if dot else f"{safe}_{n}"
            n += 1
        used.add(candidate)
        (target / candidate).write_bytes(data)

    for name, data in parsed.embedded_files:
        _write(name, data)
    for _cid, (data, _mime, name) in parsed.inline_resources.items():
        _write(name, data)


def convert_msg_to_pdf(
    msg_path: str | Path,
    pdf_path: str | Path,
    *,
    embed_attachments: bool = True,
    extract_attachments_to: str | Path | None = None,
    allow_network: bool = False,
) -> Path:
    import extract_msg

    msg_path = Path(msg_path)
    pdf_path = Path(pdf_path)

    with extract_msg.openMsg(str(msg_path)) as msg:
        parsed = parse_message(msg)

    if extract_attachments_to is not None:
        _extract_attachments_to_disk(parsed, extract_attachments_to)

    return render_pdf(
        parsed,
        pdf_path,
        base_url=str(msg_path.parent),
        embed_attachments=embed_attachments,
        allow_network=allow_network,
    )
