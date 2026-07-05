"""Pipeline metadata helpers for OKF frontmatter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.data.okf_parser import ParserOkfGitInfo

_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".asciidoc": "text/plain",
    ".adoc": "text/plain",
    ".epub": "application/epub+zip",
    ".latex": "application/x-latex",
    ".tex": "application/x-tex",
    ".vtt": "text/vtt",
}


def media_type_for_path(path: Path | str) -> str | None:
    suffix = Path(path).suffix.lower()
    return _MEDIA_TYPES.get(suffix)


def git_info() -> ParserOkfGitInfo | None:
    commit = os.environ.get("GIT_COMMIT", "").strip()
    version_label = os.environ.get("GIT_VERSION_LABEL", "").strip() or None

    if not commit:
        commit = _git_rev_parse("HEAD")

    if not commit and not version_label:
        return None

    return ParserOkfGitInfo(commit=commit or None, version_label=version_label)


def _git_rev_parse(revision: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", revision],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None
