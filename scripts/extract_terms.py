#!/usr/bin/env python3
"""Extract RU/EN terms from okf/raw/ into dictionaries/synonyms_ru_en.yaml.

Heuristic pass always runs. Optional LLM pass when OPENAI_API_KEY is set.

Usage:
  python scripts/extract_terms.py
  python scripts/extract_terms.py --okf okf/raw --output dictionaries/synonyms_ru_en.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

CYRILLIC_TERM = re.compile(r"\b[А-ЯЁ][а-яё]{3,}(?:-[А-ЯЁа-яё]+)?\b")
LATIN_TERM = re.compile(r"\b[A-Z][a-z]{3,}(?:[-/][A-Za-z]+)?\b")


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {"synonyms": {}}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if "synonyms" not in data:
        data["synonyms"] = {}
    return data


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle, allow_unicode=True, sort_keys=False)


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def heuristic_terms(okf_dir: Path) -> dict[str, list[str]]:
    ru_terms: set[str] = set()
    en_terms: set[str] = set()
    for path in sorted(okf_dir.glob("*.md")):
        body = strip_frontmatter(path.read_text(encoding="utf-8"))
        ru_terms.update(CYRILLIC_TERM.findall(body))
        en_terms.update(LATIN_TERM.findall(body))

    discovered: dict[str, list[str]] = {}
    for en in sorted(en_terms):
        key = en.lower().replace(" ", "_").replace("/", "_")
        if key not in discovered:
            discovered[key] = []
    for ru in sorted(ru_terms):
        key = ru.lower().replace(" ", "_")
        if key not in discovered:
            discovered[key] = [ru]
        elif ru not in discovered[key]:
            discovered[key].append(ru)
    return discovered


def llm_enrich(text_sample: str, existing: dict[str, list[str]]) -> dict[str, list[str]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return existing

    try:
        import urllib.request

        payload = {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract mining/metallurgy RU/EN term pairs from the text. "
                        "Return JSON object: {canonical_en: [ru_variants...]}"
                    ),
                },
                {"role": "user", "content": text_sample[:12000]},
            ],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        for key, variants in parsed.items():
            merged = existing.setdefault(str(key), [])
            for variant in variants:
                if variant not in merged:
                    merged.append(variant)
    except Exception as exc:  # noqa: BLE001
        print(f"LLM enrichment skipped: {exc}", file=sys.stderr)
    return existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap synonyms_ru_en.yaml")
    parser.add_argument("--okf", type=Path, default=Path("okf/raw"))
    parser.add_argument(
        "--output", type=Path, default=Path("dictionaries/synonyms_ru_en.yaml")
    )
    args = parser.parse_args(argv)

    if not args.okf.is_dir():
        print(f"OKF directory not found: {args.okf}", file=sys.stderr)
        return 1

    data = load_yaml(args.output)
    existing: dict[str, list[str]] = data.get("synonyms", {})

    discovered = heuristic_terms(args.okf)
    for key, variants in discovered.items():
        merged = existing.setdefault(key, [])
        for variant in variants:
            if variant not in merged:
                merged.append(variant)

    sample = "\n\n".join(
        strip_frontmatter(path.read_text(encoding="utf-8"))[:4000]
        for path in sorted(args.okf.glob("*.md"))[:5]
    )
    existing = llm_enrich(sample, existing)
    data["synonyms"] = existing
    save_yaml(args.output, data)
    print(f"Updated {args.output} ({len(existing)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
