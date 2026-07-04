# -*- coding: utf-8 -*-
"""
Инициализация сервиса онтологии при старте контейнера.

1. Создаёт базу данных из ONTOLOGY_DB_URL, если её нет (подключается к
   служебной базе postgres того же инстанса).
2. Применяет DDL, если схема experiments отсутствует.
3. Сеет реестры величин/процессов (идемпотентно).
4. При пустой базе автозагружает seed и сохранённые батчи (ontology/batches/),
   если ONTOLOGY_AUTOLOAD=1 (по умолчанию включено).

Запуск: python -m ontology.service_init  (используется в Dockerfile CMD)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from .store import DEFAULT_URL, Store


def ensure_database(url: str, attempts: int = 30) -> None:
    """CREATE DATABASE, если базы из url нет. Ретраи — ждём готовности db."""
    import psycopg
    m = re.match(r"(postgresql://[^/]+)/([^?]+)", url)
    if not m:
        raise ValueError(f"не могу разобрать ONTOLOGY_DB_URL: {url!r}")
    server, dbname = m.group(1), m.group(2)
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with psycopg.connect(f"{server}/postgres", autocommit=True) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
                ).fetchone()
                if not exists:
                    conn.execute(f'CREATE DATABASE "{dbname}"')
                    print(f"[service_init] создана база {dbname}")
                return
        except psycopg.OperationalError as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f"Postgres недоступен: {last}")


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.environ.get("ONTOLOGY_DB_URL", DEFAULT_URL)
    ensure_database(url)
    store = Store.open(url)

    has_schema = store.scalar(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_schema='experiments'")
    if not has_schema:
        store.apply_ddl()
        print("[service_init] применён DDL")

    from .loader import load_batch, seed_registries
    seed_registries(store)

    n_docs = store.scalar("SELECT count(*) FROM experiments.documents")
    autoload = os.environ.get("ONTOLOGY_AUTOLOAD", "1") == "1"
    if n_docs == 0 and autoload:
        base = Path(__file__).parent
        files = [base / "seed" / "norilsk_pgm.json",
                 *sorted((base / "batches").glob("*.json"))]
        loaded = 0
        for f in files:
            if not f.exists() or f.stem.startswith("_"):
                continue
            try:
                load_batch(store, json.loads(f.read_text(encoding="utf-8")))
                loaded += 1
            except Exception as e:
                print(f"[service_init] батч {f.name} пропущен: {e}")
        print(f"[service_init] автозагрузка: {loaded} батчей,"
              f" документов: {store.scalar('SELECT count(*) FROM experiments.documents')}")
        try:
            from .extract.quantities import migrate_db
            rep = migrate_db(store, apply=True, use_llm=False)
            print(f"[service_init] канонизация величин: {rep['resolved']} разрешено")
        except Exception as e:
            print(f"[service_init] канонизация пропущена: {e}")
    store.close()
    print("[service_init] готово")


if __name__ == "__main__":
    main()
