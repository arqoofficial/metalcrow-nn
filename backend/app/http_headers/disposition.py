"""HTTP Content-Disposition helpers."""

from __future__ import annotations

from urllib.parse import quote


def attachment_content_disposition(filename: str) -> str:
    """Build a latin-1-safe attachment header, using RFC 5987 for Unicode names."""
    escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
    try:
        escaped.encode("latin-1")
        return f'attachment; filename="{escaped}"'
    except UnicodeEncodeError:
        ascii_fallback = (
            "".join(ch if ch.isascii() else "_" for ch in filename).replace('"', "")
            or "download"
        )
        encoded = quote(filename, safe="")
        return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
