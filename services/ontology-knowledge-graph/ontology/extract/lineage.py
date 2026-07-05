# -*- coding: utf-8 -*-
"""
Извлечение lineage-отношений derived_from (уплотнение графа передела).

Авто-lineage в run.py строит рёбра только внутри эксперимента с явными ролями
input/output — таких рёбер мало. Здесь отдельный проход по документам целенаправленно
достаёт утверждения вида «X получают/производят из Y», «Y перерабатывают/конвертируют
в X» строгой JSON-схемой и превращает их в derived_from(product → source).

Оба конца канонизируются через entities.material_ext_id, поэтому новые
near-duplicate материалы не создаются — переиспользуются существующие
канонические строки. Загрузка идемпотентна: id ребра выводится из
канонических src+dst+predicate, повторные прогоны не плодят рёбер.

    python -m ontology.extract.lineage --dir "<папка с .md>" --limit 5
    python -m ontology.extract.lineage file1.md file2.md --no-load
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import entities
from .llm import FALLBACK_MODEL, _obj, _read_base_url, _read_env_key
from .parse import detect_lang, parse_document
from .relocate import relocate
from .run import BATCH_DIR, guess_family, guess_year, slugify
from ..batch import (
    BatchDocument, BatchMaterial, BatchSemanticEdge, ExtractionBatch,
)

# корпус по умолчанию (тот же, что у ingest_bridge)
DEFAULT_CORPUS = Path("C:/Users/ASUS/Array/Соревнования/"
                      "Норникель 2026 Научный клубок/data_cleaned_docling_01")

# ── JSON-схема выхода (strict) ─────────────────────────────────────────────

_REL = _obj({
    "product": {"type": "string", "description": "material that is produced/obtained (X)"},
    "source": {"type": "string", "description": "material it is produced/obtained FROM (Y)"},
    "process": {"type": "string", "description": "operation name as written, empty if absent"},
    "quote": {"type": "string", "description": "verbatim sentence stating the relation"},
})

LINEAGE_SCHEMA = _obj({"relations": {"type": "array", "items": _REL}})

LINEAGE_PROMPT = (
    "From the technical text below (Russian or English), extract material lineage "
    "relations: which material is produced/obtained FROM which other material.\n"
    "Emit a relation only when the text explicitly states it: "
    "'X получают/производят/выделяют из Y', 'Y перерабатывают/конвертируют/"
    "плавят в X', 'X is produced/obtained/recovered from Y'.\n"
    "product = the resulting material (X); source = the input material (Y).\n"
    "Rules: "
    "(1) every relation MUST carry 'quote' — a verbatim sentence copied "
    "character-for-character from the text; "
    "(2) both product and source must be concrete materials named in the text; "
    "(3) only relations explicitly stated, never infer; "
    "(4) if nothing found, return an empty array.\n\n"
    "TEXT:\n"
)


class LineageExtractor:
    """Потокобезопасный экстрактор lineage: один клиент, параллельные вызовы."""

    def __init__(self, model: str | None = None, max_tokens: int = 2000,
                 timeout: float = 120.0):
        from openai import OpenAI
        self.client = OpenAI(base_url=_read_base_url(), api_key=_read_env_key(),
                             timeout=timeout, max_retries=2)
        self.max_tokens = max_tokens
        self._lock = threading.Lock()
        available = {m.id for m in self.client.models.list()}
        want = model or FALLBACK_MODEL
        self.model = want if want in available else (
            FALLBACK_MODEL if FALLBACK_MODEL in available else sorted(available)[0])

    def extract_chunk(self, text: str) -> list[dict]:
        kwargs: dict = dict(
            model=self.model, temperature=0, max_tokens=self.max_tokens,
            response_format={"type": "json_schema", "json_schema": {
                "name": "lineage", "schema": LINEAGE_SCHEMA, "strict": True}},
            messages=[{"role": "user", "content": LINEAGE_PROMPT + text}])
        if self.model == FALLBACK_MODEL:
            kwargs["reasoning_effort"] = "low"
        r = self.client.chat.completions.create(**kwargs)
        content = r.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []
        rels = data.get("relations")
        return rels if isinstance(rels, list) else []

    def warmup(self) -> None:
        try:
            self.client.chat.completions.create(
                model=self.model, max_tokens=8, temperature=0,
                messages=[{"role": "user", "content": "ok"}])
        except Exception:
            pass


# ── сборка батча ───────────────────────────────────────────────────────────

def extract_lineage(ex: LineageExtractor, path: Path, max_chunks: int = 8,
                    workers: int = 4, okf_raw_path: str | None = None
                    ) -> ExtractionBatch:
    doc = parse_document(path)
    lang = detect_lang(doc.full_text)
    chunks = doc.chunks(max_chars=6000)[:max_chunks]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda blocks: (blocks, ex.extract_chunk(
                "\n".join(b.text for b in blocks))), chunks))

    slug = slugify(path.stem)
    doc_id = f"doc:{slug}"
    first_text = " ".join(b.text for b in doc.blocks[:3])
    batch = ExtractionBatch(
        extractor="llm_v1",
        documents=[BatchDocument(
            doc_id=doc_id, title=path.stem[:200], doc_type="article", lang=lang,
            country="RU" if lang == "ru" else "XX", year=guess_year(first_text),
            source_path=str(path), artifact_sha256=doc.artifact_sha256,
            okf_raw_path=okf_raw_path)])

    seen_mat: set[str] = set()
    seen_edge: set[tuple[str, str]] = set()

    def _material(name: str) -> str | None:
        name = (name or "").strip()
        if not name:
            return None
        cid = entities.material_ext_id(name)
        if cid not in seen_mat:
            seen_mat.add(cid)
            batch.materials.append(BatchMaterial(
                id=cid, label=entities.canonical_material(name),
                family=guess_family(name)))
        return cid

    for blocks, rels in results:
        for rel in rels:
            quote = (rel.get("quote") or "").strip()
            if not quote:
                continue                       # ребро без цитаты не берём
            product = _material(rel.get("product"))
            source = _material(rel.get("source"))
            if not product or not source or product == source:
                continue
            if (product, source) in seen_edge:
                continue
            seen_edge.add((product, source))
            # relocate валидирует, что цитата действительно есть в документе
            # (как в run.py); low-confidence — не берём (возможная галлюцинация).
            loc = relocate(quote, blocks)
            if loc.confidence_factor < 0.85:
                continue
            batch.lineage.append(BatchSemanticEdge(
                src=product, dst=source,
                process=entities.canonical_process(rel.get("process") or ""),
                snippet=quote[:400], doc_id=doc_id))
    return batch


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Извлечение derived_from lineage из документов")
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dir", type=Path, default=None,
                    help=f"взять все .md из папки (rglob; по умолчанию корпус)")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--max-chunks", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--doc-workers", type=int, default=2, help="документов параллельно")
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    files = list(args.files)
    root = args.dir if args.dir is not None else (DEFAULT_CORPUS if not files else None)
    if root is not None:
        files += sorted(root.rglob("*.md"))
    files = files[:args.limit]
    if not files:
        print("нет входных файлов"); return

    ex = LineageExtractor(model=args.model)
    print(f"модель: {ex.model} · документов: {len(files)}")
    ex.warmup()
    BATCH_DIR.mkdir(exist_ok=True)

    store = None
    load_batch = None
    if not args.no_load:
        from ..loader import load_batch, seed_registries
        from ..store import Store
        store = Store.open(args.db)
        seed_registries(store)
    db_lock = threading.Lock()

    total = {"docs": 0, "materials": 0, "lineage": 0, "loaded_edges": 0, "errors": 0}

    def one(path: Path) -> str:
        try:
            b = extract_lineage(ex, path, max_chunks=args.max_chunks)
            out = BATCH_DIR / f"lineage-{slugify(path.stem)}.json"
            out.write_text(b.model_dump_json(indent=1), encoding="utf-8")
            loaded = 0
            if store is not None:
                from ..loader import load_batch
                with db_lock:
                    rep = load_batch(store, b)
                    loaded = rep.counts.get("edges_semantic", 0)
            total["docs"] += 1
            total["materials"] += len(b.materials)
            total["lineage"] += len(b.lineage)
            total["loaded_edges"] += loaded
            return (f"  ok {path.name[:52]:<54} mat={len(b.materials):>2}"
                    f" lineage={len(b.lineage):>2} loaded_edges={loaded:>2}")
        except Exception as e:
            total["errors"] += 1
            return f"  FAIL {path.name[:52]}: {type(e).__name__}: {str(e)[:100]}"

    if args.doc_workers > 1:
        with ThreadPoolExecutor(max_workers=args.doc_workers) as pool:
            for msg in pool.map(one, files):
                print(msg, flush=True)
    else:
        for f in files:
            print(one(f), flush=True)
    print("итого:", json.dumps(total, ensure_ascii=False))
    if store is not None:
        store.close()


if __name__ == "__main__":
    main()
