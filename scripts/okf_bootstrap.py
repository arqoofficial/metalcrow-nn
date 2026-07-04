#!/usr/bin/env python3
"""Throwaway bootstrap: local_files/ -> okf/raw/*.md (SPEC_V5 Phase 0).

Uses Docling when installed; otherwise writes a readable stub so downstream
tracks (NLP dictionary, parse-docling worker) can proceed overnight.

Usage:
  uv run --with docling python scripts/okf_bootstrap.py
  python scripts/okf_bootstrap.py --input local_files --output okf/raw
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".csv"}


def try_docling_convert(path: Path) -> str | None:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None

    converter = DocumentConverter()
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def stub_markdown(path: Path) -> str:
    return (
        f"# {path.name}\n\n"
        f"> OKF bootstrap stub ({path.suffix or 'unknown'})\n\n"
        f"Source path: `{path}`\n"
    )


def write_okf_raw(output_dir: Path, source: Path, markdown: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{source.stem}.md"
    frontmatter = (
        "---\n"
        "level: raw\n"
        f"source: {source.name}\n"
        f"parsed_at: {datetime.now(tz=UTC).isoformat()}\n"
        "---\n\n"
    )
    target.write_text(frontmatter + markdown, encoding="utf-8")
    return target


def iter_inputs(input_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap OKF raw/ from local corpus")
    parser.add_argument("--input", type=Path, default=Path("local_files"))
    parser.add_argument("--output", type=Path, default=Path("okf/raw"))
    args = parser.parse_args(argv)

    if not args.input.is_dir():
        print(f"Input directory not found: {args.input}", file=sys.stderr)
        return 1

    inputs = iter_inputs(args.input)
    if not inputs:
        print(f"No supported files under {args.input}", file=sys.stderr)
        return 1

    docling_available = False
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401

        docling_available = True
    except ImportError:
        pass

    print(f"Processing {len(inputs)} file(s); docling={'yes' if docling_available else 'stub'}")

    for path in inputs:
        markdown = try_docling_convert(path)
        if markdown is None:
            markdown = stub_markdown(path)
        target = write_okf_raw(args.output, path, markdown)
        print(f"  {path.name} -> {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
