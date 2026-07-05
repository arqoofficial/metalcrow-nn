# -*- coding: utf-8 -*-
"""
Ингест онтологии из SHARED-корпуса nornickel-2026-parser — по образцу
science-knowledge-graph/scripts/ingest_shared_corpus.py.

Не live/пересчитываемый пайплайн: разовый (перезапускаемый, идемпотентный)
прогон по общему корпусу. Обходит дерево парсера (`/files/tree` под RAW_DATA и
UPLOAD_DATA), тянет OKF markdown каждого файла через `/markdown` (пропуская те,
у кого вывода ещё нет), извлекает факты онтологией и грузит в её БД. Сырой путь
SHARED пишется в okf_raw_path каждого факта → фронт строит wiki-диплинк
(/wiki?doc=<okf_raw_path>), тот же ключ, что у online L1 (documents.okf_raw_path).

Запуск — внутри контейнера ontology-knowledge-graph на сети metalcrow-net,
чтобы достучаться до парсера по http://parser-main:8114:

    docker compose exec ontology-knowledge-graph \
        python -m ontology.ingest_shared --limit 10
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .extract.run import extract_markdown_text, slugify, BATCH_DIR
from .loader import load_batch, seed_registries
from .store import Store

_TREE_ROOTS = ("RAW_DATA", "UPLOAD_DATA")
_TIMEOUT = 600


def _get(url: str, params: dict) -> tuple[int, bytes]:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full, timeout=_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def _collect_files(nodes: list[dict], prefix: str, out: list[str]) -> None:
    for n in nodes:
        path = f"{prefix}/{n['name']}" if prefix else n["name"]
        if n["type"] == "file":
            out.append(path)
        else:
            _collect_files(n.get("children") or [], path, out)


def _paginated_children(tree: dict, resolved_root: str) -> list[dict]:
    node = tree
    for part in resolved_root.split("/") if resolved_root else []:
        match = next((c for c in (node.get("children") or [])
                      if c["name"] == part), None)
        if match is None:
            return []
        node = match
    return node.get("children") or []


def walk_tree(parser_api: str, root: str) -> list[str]:
    """Сырые пути файлов (напр. 'RAW_DATA/Доклады/q1.pdf') под одним поддеревом
    SHARED. Форма ответа парсера — как в ingest_shared_corpus.py KG."""
    out: list[str] = []
    offset = 0
    while True:
        status, body = _get(f"{parser_api}/files/tree", {
            "root": root, "max_depth": 10, "include_files": True,
            "include_dirs": True, "offset": offset, "limit": 1000})
        if status == 404:
            return out
        data = json.loads(body)
        resolved_root = data["resolved_root"]
        for child in _paginated_children(data["tree"], resolved_root):
            prefix = f"{resolved_root}/{child['name']}" if resolved_root else child["name"]
            if child["type"] == "file":
                out.append(prefix)
            else:
                _collect_files(child.get("children") or [], prefix, out)
        if not data.get("has_more"):
            break
        offset = data["next_offset"]
    return out


def fetch_okf_markdown(parser_api: str, raw_path: str) -> str | None:
    """OKF markdown сырого файла или None, если вывода ещё нет (400/404)."""
    status, body = _get(f"{parser_api}/markdown", {"okf_path": raw_path})
    if status in (400, 404):
        return None
    return body.decode("utf-8", "ignore")


def ingest(parser_api: str, model: str | None, limit: int, load: bool,
           target_db: str | None, doc_workers: int) -> None:
    raw_paths: list[str] = []
    for root in _TREE_ROOTS:
        raw_paths.extend(walk_tree(parser_api, root))
    raw_paths = sorted(raw_paths)[:limit]
    print(f"из SHARED парсера: {len(raw_paths)} файлов (лимит {limit})")
    if not raw_paths:
        print("нечего обрабатывать")
        return

    if model == "mock":
        from .extract.mock import MockExtractor
        ex = MockExtractor()
    else:
        from .extract.llm import Extractor
        ex = Extractor(model=model)
        ex.warmup()
    print(f"модель: {ex.model} · doc-воркеров: {doc_workers}")

    BATCH_DIR.mkdir(exist_ok=True)
    store = Store.open(target_db) if load else None
    if store:
        seed_registries(store)
    db_lock = threading.Lock()

    def one(raw_path: str) -> str:
        text = fetch_okf_markdown(parser_api, raw_path)
        if text is None:
            return f"  skip {raw_path[:60]} (нет OKF)"
        try:
            b = extract_markdown_text(ex, text, raw_path, okf_raw_path=raw_path)
            out = BATCH_DIR / f"okf-{slugify(raw_path.replace('/', '-'))}.json"
            out.write_text(b.model_dump_json(indent=1), encoding="utf-8")
            n_m = sum(len(e.measurements) for e in b.experiments)
            if store:
                with db_lock:
                    load_batch(store, b)
            return (f"  ok {raw_path[:56]:<58} exp={len(b.experiments):>2}"
                    f" meas={n_m:>3} claims={len(b.claims):>2}")
        except Exception as e:
            return f"  FAIL {raw_path[:56]}: {type(e).__name__}: {str(e)[:100]}"

    if doc_workers > 1:
        with ThreadPoolExecutor(max_workers=doc_workers) as pool:
            for msg in pool.map(one, raw_paths):
                print(msg, flush=True)
    else:
        for p in raw_paths:
            print(one(p), flush=True)
    if store:
        store.close()


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Ингест онтологии из SHARED парсера (по образцу KG)")
    ap.add_argument("--parser-api", default="http://parser-main:8114/api/v1",
                    help="базовый URL nornickel-2026-parser (в сети metalcrow-net)")
    ap.add_argument("--model", default=None, help="mock | имя модели | пусто=авто")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--doc-workers", type=int, default=6)
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--db", default=None, help="целевая БД онтологии")
    args = ap.parse_args()
    ingest(args.parser_api, args.model, args.limit, not args.no_load,
           args.db, args.doc_workers)


if __name__ == "__main__":
    main()
