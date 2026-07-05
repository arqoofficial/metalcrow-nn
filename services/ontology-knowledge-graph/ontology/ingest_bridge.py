# -*- coding: utf-8 -*-
"""
Мост к ingest-контуру (parse-docling + experiments.documents backend'а).

Ingest-контур складывает сырые документы в MinIO, распаршенный markdown — в
OKF raw (documents.okf_raw_path), метаданные — в experiments.documents своей
БД. Этот модуль забирает оттуда работу для онтологии:

    python -m ontology.ingest_bridge --okf-root <папка с raw .md> [--model mock] [--limit 20]
    python -m ontology.ingest_bridge --source-db postgresql://... --okf-root <mount> ...

- --okf-root без --source-db: обработать все .md в папке (docling уже отработал).
- --source-db: прочитать их таблицу documents (окf_raw_path, processing_level),
  взять готовые L1+ файлы, обработать, пометить нечего — их БД bridge не пишет
  (read-only: запись только в БД онтологии).

Повторный запуск идемпотентен (id из имени файла).
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .extract.run import extract_document, slugify, BATCH_DIR
from .loader import load_batch, seed_registries
from .store import Store


def source_documents(source_db_url: str) -> list[dict]:
    """Read-only чтение реестра документов ingest-контура."""
    src = Store(source_db_url)
    try:
        return src.query(
            "SELECT id::text, filename, minio_key, okf_raw_path, processing_level"
            " FROM experiments.documents WHERE okf_raw_path IS NOT NULL"
            " ORDER BY uploaded_at")
    finally:
        src.close()


def bridge(okf_root: Path, source_db_url: str | None, model: str | None,
           limit: int, load: bool, target_db: str | None,
           okf_prefix: str = "", doc_workers: int = 1) -> None:
    # Пары (файл, okf_raw_path): путь источника кладётся в провенанс каждого
    # факта → фронт строит wiki-диплинк /wiki?doc=<okf_raw_path>.
    if source_db_url:
        docs = source_documents(source_db_url)
        pairs = [(okf_root / d["okf_raw_path"], d["okf_raw_path"])
                 for d in docs if d.get("okf_raw_path")]
        pairs = [(f, o) for f, o in pairs if f.exists()][:limit]
        print(f"из реестра ingest-контура: {len(docs)} доков,"
              f" доступно локально: {len(pairs)}")
    else:
        files = sorted(okf_root.rglob("*.md"))[:limit]
        pairs = [(f, okf_prefix + f.relative_to(okf_root).as_posix())
                 for f in files]
        print(f"из OKF-папки: {len(pairs)} .md файлов"
              + (f" · okf-префикс: {okf_prefix!r}" if okf_prefix else ""))
    if not pairs:
        print("нечего обрабатывать")
        return

    if model == "mock":
        from .extract.mock import MockExtractor
        ex = MockExtractor()
    else:
        from .extract.llm import Extractor
        ex = Extractor(model=model)
        ex.warmup()
    print(f"модель: {ex.model} · документов: {len(pairs)} · doc-воркеров: {doc_workers}")

    BATCH_DIR.mkdir(exist_ok=True)
    store = Store.open(target_db) if load else None
    if store:
        seed_registries(store)
    db_lock = threading.Lock()   # psycopg-соединение одно — запись сериализуем

    def one(pair: tuple[Path, str]) -> str:
        f, okf = pair
        try:
            b = extract_document(ex, f, okf_raw_path=okf)
            out = BATCH_DIR / f"okf-{slugify(f.stem)}.json"
            out.write_text(b.model_dump_json(indent=1), encoding="utf-8")
            n_m = sum(len(e.measurements) for e in b.experiments)
            if store:
                with db_lock:
                    load_batch(store, b)
            return (f"  ok {f.name[:56]:<58} exp={len(b.experiments):>2}"
                    f" meas={n_m:>3} claims={len(b.claims):>2}")
        except Exception as e:
            return f"  FAIL {f.name[:56]}: {type(e).__name__}: {str(e)[:100]}"

    if doc_workers > 1:
        with ThreadPoolExecutor(max_workers=doc_workers) as pool:
            for msg in pool.map(one, pairs):
                print(msg, flush=True)
    else:
        for pair in pairs:
            print(one(pair), flush=True)
    if store:
        store.close()


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--okf-root", type=Path, required=True)
    ap.add_argument("--source-db", default=None,
                    help="postgresql://... БД ingest-контура (read-only)")
    ap.add_argument("--model", default=None, help="mock | имя модели | пусто=авто")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--db", default=None, help="целевая БД онтологии")
    ap.add_argument("--okf-prefix", default="",
                    help="префикс к okf_raw_path без --source-db (напр. '01_docling_clean00/')")
    ap.add_argument("--doc-workers", type=int, default=1,
                    help="документов параллельно (LLM без лимита конкурентности)")
    args = ap.parse_args()
    bridge(args.okf_root, args.source_db, args.model, args.limit,
           not args.no_load, args.db, args.okf_prefix, args.doc_workers)


if __name__ == "__main__":
    main()
