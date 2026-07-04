"""Celery queue topology for SPEC_V5 ingestion plane (Appendix B)."""

from __future__ import annotations

from typing import Final

QUEUE_PARSE_DOCLING: Final = "parse.docling"
QUEUE_CLEAN: Final = "clean"
QUEUE_EXTRACT_SPACY: Final = "extract.spacy"
QUEUE_EXTRACT_LLM: Final = "extract.llm"
QUEUE_EMBED: Final = "embed"
QUEUE_GRAPH_SYNC: Final = "graph.sync"

QUEUE_NAMES: Final[tuple[str, ...]] = (
    QUEUE_PARSE_DOCLING,
    QUEUE_CLEAN,
    QUEUE_EXTRACT_SPACY,
    QUEUE_EXTRACT_LLM,
    QUEUE_EMBED,
    QUEUE_GRAPH_SYNC,
)

# Task name prefix -> queue. Extend as services register Celery tasks.
_TASK_QUEUE_MAP: Final[dict[str, str]] = {
    "parse.docling": QUEUE_PARSE_DOCLING,
    "clean": QUEUE_CLEAN,
    "extract.spacy": QUEUE_EXTRACT_SPACY,
    "extract.llm": QUEUE_EXTRACT_LLM,
    "embed": QUEUE_EMBED,
    "graph.sync": QUEUE_GRAPH_SYNC,
}


def queue_for_task(task_name: str) -> str:
    prefix = task_name.split(".", 1)[0]
    return _TASK_QUEUE_MAP.get(prefix, prefix)


def build_task_routes() -> dict[str, dict[str, str]]:
    """Celery `task_routes` mapping for broker configuration."""
    return {f"{prefix}.*": {"queue": queue} for prefix, queue in _TASK_QUEUE_MAP.items()}
