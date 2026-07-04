"""Read OKF Markdown via nornickel-2026-parser API."""

from app.services import parser_client


def read_okf_markdown(relative_path: str) -> str | None:
    """Load markdown body from parser SHARED; returns None if missing."""
    try:
        return parser_client.fetch_markdown(relative_path)
    except parser_client.ParserError:
        return None
