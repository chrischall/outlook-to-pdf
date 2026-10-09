from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Protocol, runtime_checkable

_log = logging.getLogger(__name__)


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
    # Visible attachment names whose content could not be read (web links,
    # broken attached messages) — listed, but neither embedded nor extracted.
    not_embedded: list[str] = field(default_factory=list)
    attachments_embedded: bool = False


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for enc in ("utf-8", "cp1252"):
            try:
                return value.decode(enc)
            except UnicodeDecodeError:
                continue
        # latin-1 maps every byte, so it is the terminal fallback.
        return value.decode("latin-1")
    s = str(value).strip()
    return s or None


# <meta charset="x"> or <meta http-equiv="Content-Type" content="...; charset=x">
_META_CHARSET_RE = re.compile(rb"""<meta\b[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.IGNORECASE)


def _decode_html(value: object) -> str | None:
    """Decode an HTML body, honouring its declared ``<meta>`` charset.

    Falls back to :func:`_coerce_str`'s utf-8 / cp1252 / latin-1 chain when
    no charset is declared, the codec is unknown, or the bytes don't match it.
    """
    if isinstance(value, (bytearray, memoryview)):
        value = bytes(value)
    if isinstance(value, bytes):
        m = _META_CHARSET_RE.search(value[:4096])
        if m:
            try:
                return value.decode(m.group(1).decode("ascii"))
            except (LookupError, UnicodeDecodeError):
                pass
    return _coerce_str(value)


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


def _attachment_bytes(raw_data: object) -> bytes | None:
    """Bytes for an attachment's ``.data``, or None if it has no content.

    An attached email (extract_msg ``EmbeddedMsgAttachment``) exposes a parsed
    Message rather than bytes; serialise it back to a standalone .msg.
    """
    if isinstance(raw_data, (bytes, bytearray, memoryview)):
        return bytes(raw_data)
    export = getattr(raw_data, "exportBytes", None)
    if callable(export):
        for allow_bad in (False, True):
            try:
                out = export(allowBadEmbed=allow_bad)
            except Exception:  # noqa: BLE001 — any failure means "try harder / give up"
                continue
            if isinstance(out, (bytes, bytearray, memoryview)):
                return bytes(out)
    return None


def parse_message(msg: _MessageLike) -> ParsedEmail:
    """Read a Message-like object into a ParsedEmail.

    Walks the attachment list exactly once, populating:
      - ``attachments`` — visible names (purely inline images are filtered out)
      - ``embedded_files`` — (name, bytes) for PDF /EmbeddedFiles
      - ``inline_resources`` — CID -> (bytes, mime, name) for cid: URL resolution
    """
    html_body = _decode_html(msg.htmlBody) if msg.htmlBody else None
    # Lower-cased: the cid: url fetcher matches case-insensitively, so the
    # "purely inline?" check must agree with it.
    haystack = (html_body or "").lower()

    attachments: list[str] = []
    embedded: list[tuple[str, bytes]] = []
    inline: dict[str, tuple[bytes, str, str]] = {}
    not_embedded: list[str] = []

    for att in (msg.attachments or []):
        name = _attachment_name(att)
        cid = _normalize_cid(getattr(att, "cid", None) or getattr(att, "contentId", None))
        mime = _coerce_str(getattr(att, "mimetype", None)) or "application/octet-stream"
        raw_data = getattr(att, "data", None)
        raw = _attachment_bytes(raw_data)
        if raw is not None and not isinstance(raw_data, (bytes, bytearray, memoryview)):
            # Attached message serialised to .msg — make the name say so.
            if not name.lower().endswith(".msg"):
                name = f"{name}.msg"

        if cid and raw is not None:
            inline[cid] = (raw, mime, name)

        # Purely inline images live in inline_resources only — no double-listing.
        if cid and f"cid:{cid.lower()}" in haystack:
            continue

        attachments.append(name)
        if raw is not None:
            embedded.append((name, raw))
        else:
            not_embedded.append(name)
            _log.warning("attachment %r has no readable content; it is listed but not embedded", name)

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
        not_embedded=not_embedded,
    )


_HTML_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)


_HTML_BODY_OPEN_RE = re.compile(r"<body\b", re.IGNORECASE)
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL)


def _extract_body_inner(html: str) -> str:
    m = _HTML_BODY_RE.search(html)
    return m.group(1) if m else html


def _extract_head_styles(html: str) -> list[str]:
    """CSS from ``<style>`` blocks before ``<body>`` — where Outlook and most
    marketing mail keep their layout CSS. Styles inside the body survive body
    extraction on their own."""
    m = _HTML_BODY_OPEN_RE.search(html)
    if not m:
        return []
    return [css for css in _STYLE_RE.findall(html[: m.start()]) if css.strip()]


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

    email_css: list[str] = []
    if parsed.html_body:
        body_html = _extract_body_inner(parsed.html_body)
        email_css = _extract_head_styles(parsed.html_body)
    else:
        body_html = _text_to_html(parsed.text_body)

    attachments_html = ""
    if parsed.attachments:
        missing = set(parsed.not_embedded)

        def item(name: str) -> str:
            flag = " <em>(not embedded &mdash; no readable content)</em>" if name in missing else ""
            return f"    <li>{escape(name)}{flag}</li>"

        items = "\n".join(item(name) for name in parsed.attachments)
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
        # Email CSS first so our header styling wins on equal specificity.
        + "".join(f"<style>{css}</style>\n" for css in email_css)
        + f"<style>{_HEADER_CSS}</style>\n"
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


_NETWORK_SCHEMES = frozenset({"http", "https"})
_PASSTHROUGH_SCHEMES = frozenset({"data"}) | _NETWORK_SCHEMES


def _make_url_fetcher(
    inline_resources: dict[str, tuple[bytes, str, str]],
    *,
    allow_network: bool,
):
    """Build a WeasyPrint url_fetcher with a strict allowlist:

    - ``cid:`` URLs resolve from ``inline_resources`` (never the network).
    - ``data:`` URLs are passed through (fully self-contained).
    - ``http`` / ``https`` pass through only when ``allow_network`` is True.
    - Everything else — ``ftp``, ``file``, ``about``, etc. — is always
      blocked, even with ``allow_network``: a crafted
      ``<a rel="attachment" href="file:///...">`` would otherwise embed an
      arbitrary local file into the PDF.

    Blocking is the default because email bodies routinely contain tracking
    pixels (http leak), `<img src="file:///etc/passwd">` style probes
    (local-file leak), or relative paths that resolve under ``base_url`` to
    something on disk. We never want to read those during render.
    """
    from weasyprint.urls import URLFetcher, URLFetcherResponse

    # Restricting the underlying fetcher too means an allowed http URL that
    # redirects to ftp:// (urllib follows those) is refused as well.
    passthrough = URLFetcher(allowed_protocols=_PASSTHROUGH_SCHEMES)

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

        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme == "data" or (allow_network and scheme in _NETWORK_SCHEMES):
            return passthrough.fetch(url)

        return _blank_png_response(url)

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

# Most filesystems cap a name at 255 bytes; stay well under so the collision
# suffix (``_12``) still fits.
_MAX_FILENAME_BYTES = 200
_MAX_EXT_BYTES = 20


def _truncate_utf8(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _truncate_filename(name: str) -> str:
    if len(name.encode("utf-8")) <= _MAX_FILENAME_BYTES:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext.encode("utf-8")) > _MAX_EXT_BYTES:
        return _truncate_utf8(name, _MAX_FILENAME_BYTES)
    suffix = "." + ext
    return _truncate_utf8(stem, _MAX_FILENAME_BYTES - len(suffix.encode("utf-8"))) + suffix


def _sanitize_filename(name: str) -> str:
    """Reduce ``name`` to a safe basename suitable for writing to disk.

    Strips directory components, control chars, and platform-specific path
    separators / drive-letter colons. The attachment filenames in a .msg are
    attacker-controlled, so this guards the sidecar extraction path against
    traversal (`../../etc/passwd`) and NUL-byte tricks, and truncates
    over-long names (keeping the extension) so ``write_bytes`` can't fail
    with ENAMETOOLONG.
    """
    # Normalize Windows separators on non-Windows hosts so we still split.
    normalized = name.replace("\\", "/")
    base = os.path.basename(normalized).strip().lstrip(".")
    safe = _UNSAFE_FILENAME_RE.sub("_", base)
    return _truncate_filename(safe) if safe else "attachment.bin"


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

    written: set[tuple[str, bytes]] = set()
    for name, data in parsed.embedded_files:
        _write(name, data)
        written.add((name, data))
    for _cid, (data, _mime, name) in parsed.inline_resources.items():
        # A CID attachment the body never references is also a visible
        # embedded file — it was already written above.
        if (name, data) in written:
            continue
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

    # Render first: a sidecar write failure (odd attachment name, full disk)
    # must not cost the user the PDF itself.
    result = render_pdf(
        parsed,
        pdf_path,
        # No base_url: relative hrefs in the body must not resolve to files
        # next to the .msg on disk.
        base_url=None,
        embed_attachments=embed_attachments,
        allow_network=allow_network,
    )

    if extract_attachments_to is not None:
        _extract_attachments_to_disk(parsed, extract_attachments_to)

    return result
