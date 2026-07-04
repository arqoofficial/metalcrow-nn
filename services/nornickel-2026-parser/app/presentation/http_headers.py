"""HTTP header helpers — paths may contain non-ASCII characters."""

from __future__ import annotations

from urllib.parse import quote, unquote

_PATH_HEADER_SAFE = "/"


def encode_path_header(path: str) -> str:
    """Percent-encode a path so it is safe for latin-1 HTTP header values."""
    return quote(path, safe=_PATH_HEADER_SAFE)


def decode_path_header(value: str) -> str:
    """Decode a path previously encoded with :func:`encode_path_header`."""
    return unquote(value)
