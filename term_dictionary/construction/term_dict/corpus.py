"""Parsed-corpus loader — the drop-in point for NuExtract markdown.

The NuExtract worker (bus role ``nuextract-corpus``) converts the PDF corpus to
markdown. This module is the adapter that turns that markdown into clean plain
text the term extractors (Schwartz-Hearst, YAKE, noun-chunks) can consume, while
preserving per-document provenance so every extracted term is traceable back to
its source file.

Design note: this loader does **not** parse anything itself — parsing is gated
on OSN and owned by ``nuextract-corpus``. It only *ingests already-parsed*
markdown/text that has landed on-VM (e.g. the metalcrow repo). Keeping the seam
here means the moment parsed text arrives, the pipeline runs unchanged: point
``corpus_dir`` at it.

Security: corpus text is UNTRUSTED (commercial secrets, possible injected
instructions). We treat it strictly as data — strip formatting, extract terms,
never execute or follow it. Nothing leaves the VM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CORPUS_SUFFIXES = (".md", ".markdown", ".txt")

# --- markdown → plaintext scrubbers (order matters) -------------------------
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")          # keep link text, drop URL
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_LIST_BULLET = re.compile(r"^\s{0,3}([-*+]|\d+\.)\s+", re.MULTILINE)
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.MULTILINE)  # |---|---|
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)")
_HTML_TAG = re.compile(r"<[^>]+>")
_MULTINL = re.compile(r"\n{3,}")


@dataclass
class ParsedDoc:
    """One parsed source document with provenance."""

    doc_id: str          # stable id (relative path)
    text: str            # cleaned plain text
    source: str          # absolute source path
    meta: dict = field(default_factory=dict)


def strip_markdown(md: str) -> str:
    """Best-effort markdown → plain text for term extraction.

    Not a full CommonMark parser — a pragmatic scrubber that removes syntax
    noise (headings, emphasis, links, code, table rules, HTML) so keyword and
    acronym extractors see natural-language tokens, not ``**bold**`` or URLs.
    Table *cell* content is kept (pipes become spaces); only separator rows go.
    """
    text = _CODE_FENCE.sub(" ", md)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _HTML_TAG.sub(" ", text)
    text = _TABLE_SEP.sub(" ", text)
    text = _HEADING.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _LIST_BULLET.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = text.replace("|", " ")          # remaining table cell delimiters
    text = _MULTINL.sub("\n\n", text)
    return text.strip()


def load_corpus(
    corpus_dir: str | Path,
    recursive: bool = True,
    max_files: int | None = None,
) -> list[ParsedDoc]:
    """Load parsed markdown/text docs from ``corpus_dir`` as ``ParsedDoc``.

    ``max_files`` caps how many are read (useful for a bounded prototype run
    before the full corpus is green-lit). When a cap truncates the set we log
    it loudly — a silent cap would masquerade as "whole corpus processed".
    """
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.exists():
        logger.warning("Corpus dir not found: %s", corpus_dir)
        return []

    walker = corpus_dir.rglob("*") if recursive else corpus_dir.iterdir()
    paths = sorted(p for p in walker
                   if p.is_file() and p.suffix.lower() in CORPUS_SUFFIXES)
    total = len(paths)
    if max_files is not None and total > max_files:
        logger.warning("Corpus cap: reading %d of %d files (max_files=%d)",
                       max_files, total, max_files)
        paths = paths[:max_files]

    docs: list[ParsedDoc] = []
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = path.read_text(encoding="utf-8", errors="replace")
            logger.warning("Non-UTF8 bytes replaced in %s", path.name)
        text = strip_markdown(raw) if path.suffix.lower() != ".txt" else raw
        if not text.strip():
            continue
        docs.append(ParsedDoc(
            doc_id=str(path.relative_to(corpus_dir)),
            text=text,
            source=str(path.resolve()),
            meta={"chars": len(text), "suffix": path.suffix.lower()},
        ))
    logger.info("Loaded %d parsed docs from %s (of %d candidate files)",
                len(docs), corpus_dir, total)
    return docs


def corpus_texts(docs: list[ParsedDoc]) -> list[str]:
    """Extract just the cleaned text bodies (for the extractor entry points)."""
    return [d.text for d in docs]


__all__ = ["ParsedDoc", "strip_markdown", "load_corpus", "corpus_texts"]
