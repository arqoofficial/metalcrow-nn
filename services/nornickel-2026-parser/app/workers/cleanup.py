"""Advanced cleanup for Docling markdown output."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import List

CLEANER_VERSION = "1.0.0"

_LETTER = r"A-Za-z\u0400-\u04FF0-9"
_WORD_START = re.compile(rf"^[{_LETTER}]")


@dataclass
class MdCleanupConfig:
    page_boundary_depth: int = 3
    repeated_min_pages: int = 3
    drop_noise_lines: bool = True
    dedupe_consecutive: bool = True
    keep_form_feed: bool = False
    collapse_blank_max: int = 2


def clean_docling_markdown(text: str, cfg: MdCleanupConfig | None = None) -> str:
    settings = cfg or MdCleanupConfig()
    text = _normalize_text(text)
    pages = _split_pages(text)

    repeated_boundary = _detect_repeated_boundary_lines(
        pages, depth=settings.page_boundary_depth, min_pages=settings.repeated_min_pages
    )

    cleaned_pages = []
    for page in pages:
        cleaned_pages.append(
            _remove_page_noise(
                page,
                repeated_boundary=repeated_boundary,
                depth=settings.page_boundary_depth,
                drop_noise_lines=settings.drop_noise_lines,
            )
        )

    separator = "\n\f\n" if settings.keep_form_feed else "\n\n"
    text2 = separator.join("\n".join(page) for page in cleaned_pages)

    lines = text2.splitlines()
    lines = _rebuild_paragraphs_preserving_markdown(lines)

    if settings.dedupe_consecutive:
        lines = _dedupe_consecutive(lines)

    out = _collapse_blank_lines(lines, settings.collapse_blank_max)
    return "\n".join(out).strip() + "\n"


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = text.replace("\xa0", " ")
    text = "".join(ch for ch in text if ch in "\n\t\f" or ord(ch) >= 32)
    text = re.sub(rf"([{_LETTER}])-\n([{_LETTER}])", r"\1\2", text)
    return text


def _split_pages(text: str) -> List[List[str]]:
    if "\f" in text:
        return [page.splitlines() for page in text.split("\f")]

    page_break_re = re.compile(
        r"(?im)^\s*(?:---+\s*)?(?:page\s+\d+(?:\s*/\s*\d+)?)\s*(?:---+)?\s*$"
    )
    pages: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if page_break_re.match(line):
            pages.append(current)
            current = []
        else:
            current.append(line)
    pages.append(current)
    return pages


def _detect_repeated_boundary_lines(pages: List[List[str]], depth: int, min_pages: int) -> set[str]:
    counts: Counter[str] = Counter()

    for page in pages:
        nonempty_idx = [index for index, line in enumerate(page) if line.strip()]
        if not nonempty_idx:
            continue

        top = nonempty_idx[:depth]
        bottom = nonempty_idx[-depth:] if len(nonempty_idx) > depth else []

        for index in top + bottom:
            norm = _normalize_repeat_key(page[index])
            if norm:
                counts[norm] += 1

    return {key for key, value in counts.items() if value >= min_pages}


def _normalize_repeat_key(line: str) -> str:
    normalized = line.strip().lower()
    if not normalized:
        return ""
    normalized = re.sub(r"\d+", "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^\w\s#:/().,%\-]", "", normalized, flags=re.UNICODE)
    return normalized.strip()


def _remove_page_noise(
    page: List[str],
    *,
    repeated_boundary: set[str],
    depth: int,
    drop_noise_lines: bool,
) -> List[str]:
    out: list[str] = []
    nonempty_idx = [index for index, line in enumerate(page) if line.strip()]
    top_set = set(nonempty_idx[:depth])
    bottom_set = set(nonempty_idx[-depth:]) if len(nonempty_idx) > depth else set()

    for index, line in enumerate(page):
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue

        norm = _normalize_repeat_key(line)

        if (index in top_set or index in bottom_set) and norm in repeated_boundary:
            continue
        if _looks_like_page_number(stripped):
            continue
        if _looks_like_watermark_or_artifact(stripped):
            continue
        if drop_noise_lines and _is_noise_line(stripped):
            continue

        out.append(line)

    return out


def _looks_like_page_number(value: str) -> bool:
    patterns = [
        r"^(?:page|стр\.?|с\.?)\s*\d+\s*(?:/|of|из)?\s*\d*$",
        r"^\d+\s*/\s*\d+$",
        r"^\[\s*\d+\s*\]$",
        r"^-\s*\d+\s*-$",
        r"^\d+$",
    ]
    lowered = value.lower()
    return any(re.fullmatch(pattern, lowered) for pattern in patterns)


def _looks_like_watermark_or_artifact(value: str) -> bool:
    lowered = value.lower()
    bad_tokens = ("confidential", "copyright", "all rights reserved", "scanned by")
    return any(token in lowered for token in bad_tokens) and len(lowered) < 120


def _is_noise_line(value: str) -> bool:
    if _is_structural_line(value):
        return False

    if len(value) >= 8:
        alnum = sum(character.isalnum() for character in value)
        ratio = alnum / max(len(value), 1)
        if ratio < 0.30:
            return True

    if re.fullmatch(r"(.)\1{6,}", value):
        return True

    return False


def _rebuild_paragraphs_preserving_markdown(lines: List[str]) -> List[str]:
    out: list[str] = []
    buffer: list[str] = []
    in_code_fence = False

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        out.extend(_merge_paragraph_buffer(buffer))
        buffer = []

    total = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        next_line = lines[index + 1].strip() if index + 1 < total else ""

        if re.match(r"^\s*```", line):
            flush_buffer()
            out.append(line.rstrip())
            in_code_fence = not in_code_fence
            continue

        if in_code_fence:
            out.append(line.rstrip())
            continue

        if not stripped:
            flush_buffer()
            out.append("")
            continue

        if _is_structural_line(stripped) or _is_setext_underline(stripped):
            flush_buffer()
            out.append(line.rstrip())
            continue

        if _is_setext_underline(next_line):
            flush_buffer()
            out.append(line.rstrip())
            continue

        buffer.append(line.rstrip())

    flush_buffer()
    return out


def _merge_paragraph_buffer(buffer: List[str]) -> List[str]:
    if not buffer:
        return []

    merged = [buffer[0].strip()]

    for current_raw in buffer[1:]:
        current = current_raw.strip()
        previous = merged[-1]

        if _should_keep_newline_between(previous, current):
            merged.append(current)
            continue

        if previous.endswith("-") and _WORD_START.match(current):
            merged[-1] = previous[:-1] + current
        else:
            merged[-1] = previous + " " + current

    return merged


def _should_keep_newline_between(previous: str, current: str) -> bool:
    if previous.endswith("  ") or previous.endswith("\\"):
        return True
    if previous.endswith(":") and len(previous) <= 80:
        return True
    if _looks_like_title_line(previous) and len(previous.split()) <= 12:
        return True
    if _is_structural_line(current):
        return True
    return False


def _looks_like_title_line(value: str) -> bool:
    if re.search(r"[.!?]$", value):
        return False
    words = re.findall(rf"[{_LETTER}]+", value)
    if not words or len(words) > 12:
        return False
    caps = sum(word[:1].isupper() for word in words if word)
    return caps / len(words) >= 0.7


def _is_structural_line(value: str) -> bool:
    return any(
        [
            bool(re.match(r"^#{1,6}\s+\S", value)),
            bool(re.match(r"^\s*[-*+]\s+\S", value)),
            bool(re.match(r"^\s*\d{1,3}[.)]\s+\S", value)),
            bool(re.match(r"^\s*>\s+\S", value)),
            bool(re.match(r"^\s*([-*_])\1{2,}\s*$", value)),
            bool(re.match(r"^\s*\|.*\|\s*$", value)),
            bool(re.match(r"^\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*$", value)),
            bool(re.match(r"^\s{4,}\S", value)),
            bool(re.match(r"^\s*!\[.*\]\(.*\)\s*$", value)),
            bool(re.match(r"^\s*\[.{1,80}\]:\s+\S+", value)),
        ]
    )


def _is_setext_underline(value: str) -> bool:
    return bool(re.match(r"^\s*(=+|-+)\s*$", value))


def _dedupe_consecutive(lines: List[str]) -> List[str]:
    if not lines:
        return lines
    out = [lines[0]]
    for line in lines[1:]:
        if line.strip() and out[-1].strip() and line.strip() == out[-1].strip():
            continue
        out.append(line)
    return out


def _collapse_blank_lines(lines: List[str], max_blank: int) -> List[str]:
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
            out.append(line.rstrip())
        else:
            blank_run += 1
            if blank_run <= max_blank:
                out.append("")
    return out
