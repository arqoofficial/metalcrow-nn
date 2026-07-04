"""Seed gazetteer from the provided glossaries.

The thematic taxonomy + material/equipment handbooks + staff/lab registry are
*already a glossary* — the cheapest, highest-precision term source. This module
loads them into ``(term, label, lang)`` seed rows without any model call.

Supported inputs (auto-detected by extension), all optional so the pipeline
runs on whatever subset has landed:

- ``*.csv`` / ``*.tsv`` with columns ``term,label[,lang]`` (header required).
- ``*.json`` mapping ``{"MATERIAL": ["никель", "nickel"], ...}`` (label→terms)
  or a list of ``{"term":..., "label":..., "lang":...}`` rows.
- ``*.jsonl`` with one ``{"term","label","lang"}`` object per line.

Anything the team exports from the handbooks can be shaped into one of these;
the loader is deliberately forgiving so we are not blocked on a fixed schema.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import ENTITY_LABELS, UNKNOWN_LABEL
from .term_extract import detect_lang

logger = logging.getLogger(__name__)

_VALID_LABELS = set(ENTITY_LABELS) | {UNKNOWN_LABEL}


@dataclass(frozen=True)
class SeedTerm:
    term: str
    label: str
    lang: str


def _clean_label(raw: str | None) -> str:
    if not raw:
        return UNKNOWN_LABEL
    up = raw.strip().upper()
    return up if up in _VALID_LABELS else UNKNOWN_LABEL


def _mk(term: str, label: str | None, lang: str | None) -> SeedTerm | None:
    term = (term or "").strip()
    if not term:
        return None
    return SeedTerm(term=term, label=_clean_label(label), lang=lang or detect_lang(term))


def _load_csv(path: Path, delimiter: str) -> list[SeedTerm]:
    rows: list[SeedTerm] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        for row in reader:
            norm = {(k or "").strip().lower(): v for k, v in row.items()}
            st = _mk(norm.get("term", ""), norm.get("label"), norm.get("lang"))
            if st:
                rows.append(st)
    return rows


def _load_json(path: Path) -> list[SeedTerm]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[SeedTerm] = []
    if isinstance(data, dict):  # {label: [terms]}
        for label, terms in data.items():
            for term in terms or []:
                st = _mk(term, label, None)
                if st:
                    rows.append(st)
    elif isinstance(data, list):  # [{term,label,lang}]
        for obj in data:
            if isinstance(obj, dict):
                st = _mk(obj.get("term", ""), obj.get("label"), obj.get("lang"))
                if st:
                    rows.append(st)
    return rows


def _load_jsonl(path: Path) -> list[SeedTerm]:
    rows: list[SeedTerm] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        st = _mk(obj.get("term", ""), obj.get("label"), obj.get("lang"))
        if st:
            rows.append(st)
    return rows


def load_seed_file(path: str | Path) -> list[SeedTerm]:
    """Load one glossary file, dispatching on extension."""
    path = Path(path)
    if not path.exists():
        logger.warning("Seed file not found: %s", path)
        return []
    suffix = path.suffix.lower()
    if suffix == ".csv":
        rows = _load_csv(path, ",")
    elif suffix in (".tsv", ".tab"):
        rows = _load_csv(path, "\t")
    elif suffix == ".json":
        rows = _load_json(path)
    elif suffix == ".jsonl":
        rows = _load_jsonl(path)
    else:
        logger.warning("Unsupported seed format: %s", path)
        return []
    logger.info("Loaded %d seed terms from %s", len(rows), path.name)
    return rows


def load_seed_dir(directory: str | Path) -> list[SeedTerm]:
    """Load and dedupe seed terms from every glossary file in a directory."""
    directory = Path(directory)
    merged: dict[tuple[str, str], SeedTerm] = {}
    if not directory.exists():
        logger.warning("Seed dir not found: %s", directory)
        return []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in (".csv", ".tsv", ".tab", ".json", ".jsonl"):
            for st in load_seed_file(path):
                # A term with a known label beats an UNKNOWN duplicate.
                key = (st.term.casefold(), st.lang)
                if key not in merged or (merged[key].label == UNKNOWN_LABEL
                                         and st.label != UNKNOWN_LABEL):
                    merged[key] = st
    logger.info("Seed dir %s → %d unique terms", directory, len(merged))
    return list(merged.values())


__all__ = ["SeedTerm", "load_seed_file", "load_seed_dir"]
