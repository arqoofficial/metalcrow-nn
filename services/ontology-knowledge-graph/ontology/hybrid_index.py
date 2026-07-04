# -*- coding: utf-8 -*-
"""Гибридный индекс пассажей для ретрива: BM25-семейство (Postgres ts_rank) +
плотные эмбеддинги (pgvector), слитые через Reciprocal Rank Fusion.

Материализует пассажи (выводы + измерения) в таблицу `experiments.passage_index`
с дословным текстом, документом-источником, флагом «есть число» и вектором
(fastembed, ONNX — без torch). Векторный поиск — brute-force `<=>` по ~2k строк
(индекс не нужен на таком объёме).

CLI:  python -m ontology.hybrid_index --rebuild --embed
Поиск живёт в query.search_passages (этот модуль — только индекс + модель).
"""
from __future__ import annotations

import os
import threading

from .store import Store

EMBED_MODEL = os.environ.get(
    "ONTOLOGY_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
VECTOR_DIM = 768

_model = None
_model_lock = threading.Lock()
_model_failed = False


def get_model():
    """Ленивая потокобезопасная загрузка fastembed-модели. None при ошибке —
    тогда ретрив деградирует до чисто лексического (без регресса)."""
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    with _model_lock:
        if _model is None and not _model_failed:
            try:
                from fastembed import TextEmbedding
                _model = TextEmbedding(EMBED_MODEL)
            except Exception:
                _model_failed = True
                _model = None
    return _model


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    model = get_model()
    if model is None:
        return None
    return [list(map(float, v)) for v in model.embed(texts)]


def embed_query(text: str) -> list[float] | None:
    out = embed_texts([text])
    return out[0] if out else None


def vec_literal(v: list[float]) -> str:
    """pgvector-литерал '[..]' для параметра ::vector."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


_DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS experiments.passage_index (
  id         bigserial PRIMARY KEY,
  source     text NOT NULL,
  ref_id     uuid,
  doc_name   text,
  kind       text,
  text       text,
  snippet    text,
  has_number boolean DEFAULT false,
  embedding  vector({VECTOR_DIM})
);
CREATE INDEX IF NOT EXISTS passage_index_tsv ON experiments.passage_index
  USING gin (to_tsvector('russian', coalesce(text,'') || ' ' || coalesce(snippet,'')));
"""


def ensure_index(store: Store) -> None:
    for stmt in _DDL.split(";"):
        if stmt.strip():
            store.execute(stmt)
    store.commit()


def rebuild_rows(store: Store) -> int:
    """Пересобрать строки индекса из conclusions + results (без эмбеддингов).
    Сырые чанки (source='doc_chunk') НЕ трогаем — они переиндексируются отдельно
    (index_raw_chunks), иначе полнотекстовое покрытие терялось бы при каждом
    пересборе фактов."""
    store.execute("DELETE FROM experiments.passage_index WHERE source <> 'doc_chunk'")
    store.execute("""
        INSERT INTO experiments.passage_index
            (source, ref_id, doc_name, kind, text, snippet, has_number)
        SELECT 'conclusion', c.id, d.filename, c.kind, c.text, c.prov->>'snippet',
               (coalesce(c.text,'') || ' ' || coalesce(c.prov->>'snippet','')) ~ '[0-9]'
        FROM experiments.conclusions c
        LEFT JOIN experiments.experiments e ON e.id = c.experiment_id
        LEFT JOIN experiments.documents d
               ON d.id = COALESCE(c.document_id, e.document_id)
        WHERE c.superseded_by IS NULL""")
    store.execute("""
        INSERT INTO experiments.passage_index
            (source, ref_id, doc_name, kind, text, snippet, has_number)
        SELECT 'measurement', r.id, d.filename, 'measurement',
               r.quantity_kind || ' = ' ||
                 coalesce(r.value_nominal::text,
                   coalesce(r.value_min::text,'') || '-' || coalesce(r.value_max::text,''))
                 || ' ' || coalesce(r.unit,''),
               r.prov->>'snippet', true
        FROM experiments.results r
        LEFT JOIN experiments.experiments e ON e.id = r.experiment_id
        LEFT JOIN experiments.documents d ON d.id = e.document_id
        WHERE r.superseded_by IS NULL AND r.prov->>'snippet' IS NOT NULL""")
    store.commit()
    return store.scalar("SELECT count(*) FROM experiments.passage_index") or 0


def embed_missing(store: Store, batch: int = 64) -> int:
    """Досчитать эмбеддинги для строк без вектора. Идемпотентно."""
    if get_model() is None:
        return 0
    rows = store.query(
        "SELECT id, left(coalesce(snippet, text), 500) AS body"
        " FROM experiments.passage_index WHERE embedding IS NULL AND"
        " coalesce(snippet, text) IS NOT NULL")
    done = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        vecs = embed_texts([r["body"] for r in chunk])
        if vecs is None:
            break
        for r, v in zip(chunk, vecs):
            store.execute(
                "UPDATE experiments.passage_index SET embedding = %s::vector WHERE id = %s",
                (vec_literal(v), r["id"]))
        store.commit()
        done += len(chunk)
    return done


import re as _re


def _clean_name(stem: str) -> str:
    base = _re.split(r"[\\/]", stem)[-1]
    return _re.sub(r"\.(md|pdf|docx?|pptx?|txt)$", "", base, flags=_re.I).strip() or base


def chunk_markdown(text: str, size: int = 700) -> list[str]:
    """Распарсенный .md → пассажи ~size символов по границам абзацев.
    Снимает YAML-frontmatter, html-комментарии и картинки-плейсхолдеры."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    text = _re.sub(r"<!--.*?-->", " ", text, flags=_re.S)
    text = _re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    paras = [p.strip() for p in _re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            chunks.append(cur.strip())
            cur = ""
        cur = (cur + " " + p).strip()
        while len(cur) > size * 1.6:            # очень длинный абзац/таблица — режем
            chunks.append(cur[:size].strip())
            cur = cur[size:]
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if len(c) > 40]


def index_raw_chunks(store: Store, files: list) -> int:
    """Проиндексировать СЫРОЙ текст документов (полнотекстовое покрытие поверх
    извлечённых фактов): retrieval перестаёт быть ограничен экстракцией — в индекс
    попадает весь текст (напр. SAVMIN/эттрингит, которых нет среди фактов).
    Идемпотентно: строки source='doc_chunk' пересобираются."""
    from pathlib import Path
    ensure_index(store)
    store.execute("DELETE FROM experiments.passage_index WHERE source='doc_chunk'")
    store.commit()
    n = 0
    for f in files:
        f = Path(f)
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        name = _clean_name(f.stem)
        for ch in chunk_markdown(text):
            store.execute(
                "INSERT INTO experiments.passage_index"
                " (source, doc_name, kind, text, snippet, has_number)"
                " VALUES ('doc_chunk', %s, 'chunk', %s, %s, %s)",
                (name, ch, ch[:300], bool(_re.search(r"\d", ch))))
            n += 1
        store.commit()
    return n


def index_ready(store: Store) -> bool:
    try:
        return bool(store.scalar(
            "SELECT count(*) FROM experiments.passage_index WHERE embedding IS NOT NULL"))
    except Exception:
        store.rollback()
        return False


def build_and_embed(store: Store) -> dict:
    ensure_index(store)
    n = rebuild_rows(store)
    e = embed_missing(store)
    return {"rows": n, "embedded": e}


def _bg_build(db_url: str | None) -> None:
    try:
        s = Store.open(db_url)
        try:
            ensure_index(s)
            if not s.scalar("SELECT count(*) FROM experiments.passage_index"):
                rebuild_rows(s)
            embed_missing(s)
        finally:
            s.close()
    except Exception:
        pass


def start_background_build(db_url: str | None = None) -> None:
    """Неблокирующая сборка индекса при старте сервиса — healthcheck не ждёт
    эмбеддинги; до готовности ретрив работает лексически."""
    threading.Thread(target=_bg_build, args=(db_url,), daemon=True).start()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--raw-chunks-dir", default=None,
                    help="папка с .md — проиндексировать сырой текст документов")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    store = Store.open(args.db)
    ensure_index(store)
    if args.rebuild:
        print("rows:", rebuild_rows(store))
    if args.raw_chunks_dir:
        from pathlib import Path
        files = sorted(Path(args.raw_chunks_dir).rglob("*.md"))
        print("raw_chunks:", index_raw_chunks(store, files))
    if args.embed:
        print("embedded:", embed_missing(store))
    print("ready:", index_ready(store))
    store.close()


if __name__ == "__main__":
    main()
