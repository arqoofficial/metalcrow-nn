# -*- coding: utf-8 -*-
"""
Хранилище онтологии — PostgreSQL (psycopg3).

Канон схемы — ddl.sql (schema experiments.*). Подключение:
    store = Store.open()                       # env ONTOLOGY_DB_URL или дефолт
    store = Store.open("postgresql://...")     # явно

Для тестов: store.reset() пересоздаёт схему experiments с нуля.
Дев-контейнер: docker run -d --name onto_pg -p 56543:5432 \
    -e POSTGRES_PASSWORD=onto -e POSTGRES_DB=onto pgvector/pgvector:pg18
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

DDL_PATH = Path(__file__).parent / "ddl.sql"
DEFAULT_URL = os.environ.get(
    "ONTOLOGY_DB_URL", "postgresql://postgres:onto@localhost:56543/onto")


class Store:
    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self.url = url
        self._conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)

    @staticmethod
    def open(url: str | None = None) -> "Store":
        return Store(url or DEFAULT_URL)

    # ── схема ────────────────────────────────────────────────────────────
    def apply_ddl(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(DDL_PATH.read_text(encoding="utf-8"))
        self._conn.commit()

    def reset(self) -> None:
        """Снести и пересоздать схему experiments (тесты, переиндексация)."""
        with self._conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS experiments CASCADE")
        self._conn.commit()
        self.apply_ddl()

    # ── доступ ───────────────────────────────────────────────────────────
    # При ошибке SQL транзакция откатывается сразу: иначе connection застревает
    # в InFailedSqlTransaction и валит каскадом все последующие вызовы.
    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, tuple(params))
        except Exception:
            self._conn.rollback()
            raise

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.executemany(sql, rows)
        except Exception:
            self._conn.rollback()
            raise

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            self._conn.rollback()
            raise

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        rows = self.query(sql, params)
        return next(iter(rows[0].values())) if rows else None

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.commit() if exc[0] is None else self.rollback()
        self.close()

    # ── значения ─────────────────────────────────────────────────────────
    @staticmethod
    def jsondump(obj: Any) -> str:
        """Для записи в JSONB-параметр."""
        return json.dumps(obj, ensure_ascii=False)
