# -*- coding: utf-8 -*-
"""
Раннер экстракции: документы → ExtractionBatch (JSON) → (опц.) Postgres.

    python -m ontology.extract.run --dir "<папка>" --limit 25 --load
    python -m ontology.extract.run file1.docx file2.pdf --load

Каждый документ обрабатывается независимо: парсинг → чанки → LLM (параллельно)
→ нормализация значений/единиц → relocate цитат → батч. Ошибка одного документа
не останавливает остальные. Батчи сохраняются в ontology/batches/<slug>.json —
их можно перезагружать loader'ом без повторных LLM-вызовов.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..batch import (
    BatchConclusion, BatchDocument, BatchDocumentClaim, BatchEffect,
    BatchMaterial, BatchMaterialUse, BatchMeasurement, BatchSemanticEdge,
    ExtractionBatch, BatchExperiment,
)
from . import entities
from .llm import Extractor
from .normalize import map_process, normalize_unit, parse_value
from .parse import detect_lang, parse_document
from .quantities import canonize
from .relocate import relocate

BATCH_DIR = Path(__file__).resolve().parents[1] / "batches"

_FAMILY_HINTS = [
    ("концентрат", "concentrate"), ("штейн", "matte"), ("файнштейн", "fine_matte"),
    ("шлак", "slag"), ("раствор", "solution"), ("электролит", "solution"),
    ("вода", "solution"), ("кислота", "reagent"), ("реагент", "reagent"),
    ("сульфит", "reagent"), ("флюс", "reagent"), ("уголь", "reagent"),
    ("руда", "ore"), ("сплав", "alloy"), ("сталь", "steel"), ("анод", "metal"),
    ("катод", "metal"), ("осадок", "solid"), ("газ", "gas"),
]


def slugify(text: str, maxlen: int = 48) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = re.sub(r"[^\w]+", "-", t, flags=re.UNICODE).strip("-")
    return t[:maxlen].strip("-") or "doc"


def guess_family(name: str) -> str:
    low = name.lower()
    for hint, fam in _FAMILY_HINTS:
        if hint in low:
            return fam
    return "other"


def guess_year(text: str) -> int | None:
    m = re.search(r"\b(19[89]\d|20[0-2]\d)\b", text[:6000])
    return int(m.group(0)) if m else None


def _temp_step(process_raw: str, temp_text: str, dur_text: str) -> dict:
    step: dict = {"process_type": map_process(process_raw),
                  "extra": {"raw_process": process_raw}}
    v, _ = parse_value(temp_text)
    if v is not None:                     # температуры в текстах — °C → K
        _, _, vk = normalize_unit("°C", v)
        step["temperature"] = json.loads(vk.model_dump_json())
    if temp_text:
        step["extra"]["temperature_text"] = temp_text
    if dur_text:
        step["extra"]["duration_text"] = dur_text
    return step


def assemble_batch(doc_path: Path, chunks_out: list[tuple[list, dict]],
                   lang: str, sha: str, extractor_name: str,
                   okf_raw_path: str | None = None) -> ExtractionBatch:
    """Сырые ответы LLM по чанкам → валидный ExtractionBatch одного документа."""
    slug = slugify(doc_path.stem)
    doc_id = f"doc:{slug}"
    full_blocks = [b for blocks, _ in chunks_out for b in blocks]
    first_text = " ".join(b.text for b in full_blocks[:3])

    batch = ExtractionBatch(
        extractor=extractor_name,
        documents=[BatchDocument(
            doc_id=doc_id, title=doc_path.stem[:200],
            doc_type="article", lang=lang,
            country="RU" if lang == "ru" else "XX",   # XX = зарубежное, страна не установлена
            year=guess_year(first_text),
            source_path=str(doc_path), artifact_sha256=sha,
            okf_raw_path=okf_raw_path)])

    mat_ids: dict[str, str] = {}          # сырое имя.lower() → канонический ext_id
    seen_mat: set[str] = set()            # канонические id, уже добавленные в батч
    exp_n = 0
    seen_claims: set[str] = set()
    for blocks, data in chunks_out:
        for c in data.get("claims", []):
            quote = (c.get("quote") or "").strip()
            text = (c.get("text") or "").strip()
            if not quote or not text or text.lower() in seen_claims:
                continue                       # чанки перекрываются — дедуп по тексту
            seen_claims.add(text.lower())
            eff = None
            if c.get("property") and c.get("direction"):
                eff = BatchEffect(quantity_kind=(canonize(c["property"]).kind
                                                 or c["property"])[:64],
                                  direction=c["direction"],
                                  factor=c.get("factor") or "не указан")
            proc = map_process(c.get("process") or "")
            loc = relocate(quote, blocks)
            batch.claims.append(BatchDocumentClaim(
                document_id=doc_id, text=text[:800],
                kind=c.get("kind") or "finding",
                process=proc if proc != "other" else None, effect=eff,
                snippet=quote[:600], locator_kind=loc.locator_kind,
                locator=loc.locator,
                confidence=round(0.9 * loc.confidence_factor, 2)))

        for raw in data.get("experiments", []):
            mats: list[BatchMaterialUse] = []
            for mu in raw.get("materials", []):
                name = (mu.get("name") or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key not in mat_ids:
                    # Глобальный канонический id (OSN-дедуп): варианты одного
                    # материала сходятся в один id и переиспользуют строку.
                    mat_ids[key] = entities.material_ext_id(name)
                cid = mat_ids[key]
                if cid not in seen_mat:
                    seen_mat.add(cid)
                    batch.materials.append(BatchMaterial(
                        id=cid, label=name, family=guess_family(name)))
                mats.append(BatchMaterialUse(material_id=cid,
                                             role=mu.get("role") or "sample"))

            def _mat_ref(name: str) -> str | None:
                key = (name or "").strip().lower()
                if key in mat_ids:
                    return mat_ids[key]
                for k, v in mat_ids.items():
                    if key and (key in k or k in key):
                        return v
                return None

            meas: list[BatchMeasurement] = []
            for m in raw.get("measurements", []):
                quote = (m.get("quote") or "").strip()
                if not quote:
                    continue                       # факт без цитаты не берём
                canon = canonize(m.get("property") or "", m.get("unit") or "")
                if canon.method == "junk":
                    continue                       # мусор схемы («string») не тащим
                v, unc = parse_value(m.get("value"))
                unit, scale, v = normalize_unit(m.get("unit"), v)
                if v is None and canon.kind != "qualitative_observation":
                    continue    # «измерение» без числа — не измерение (ГОСТ-ссылки и т.п.)
                loc = relocate(quote, blocks)
                mid = _mat_ref(m.get("material"))
                conditions = {"subject": canon.subject} if canon.subject else {}
                meas.append(BatchMeasurement(
                    quantity_kind=(canon.kind or m.get("property") or "unknown")[:64],
                    scope="material" if mid else "experiment",
                    material_id=mid, value=v, unit=unit, scale=scale,
                    uncertainty=unc, conditions=conditions, snippet=quote[:600],
                    locator_kind=loc.locator_kind, locator=loc.locator,
                    confidence=round(0.9 * loc.confidence_factor
                                     * (canon.confidence or 1.0), 2)))

            concl: list[BatchConclusion] = []
            for c in raw.get("conclusions", []):
                quote = (c.get("quote") or "").strip()
                text = (c.get("text") or "").strip()
                if not quote or not text:
                    continue
                eff = None
                if c.get("property") and c.get("direction"):
                    eff = BatchEffect(quantity_kind=(canonize(c["property"]).kind
                                                     or c["property"])[:64],
                                      direction=c["direction"],
                                      factor=c.get("factor") or "не указан")
                loc = relocate(quote, blocks)
                concl.append(BatchConclusion(
                    text=text[:800], kind=c.get("kind") or "finding", effect=eff,
                    snippet=quote[:600], locator_kind=loc.locator_kind,
                    locator=loc.locator,
                    confidence=round(0.9 * loc.confidence_factor, 2)))

            if not (meas or concl or mats):
                continue                           # пустой «эксперимент» не тащим
            exp_id = f"exp:{slug}:{exp_n}"
            exp_n += 1
            equote = (raw.get("quote") or "").strip() or (raw.get("label") or exp_id)
            loc = relocate(equote, blocks)
            batch.experiments.append(BatchExperiment(
                id=exp_id, document_id=doc_id, title=(raw.get("label") or "")[:200],
                regime={"steps": [_temp_step(raw.get("process") or "",
                                             raw.get("temperature") or "",
                                             raw.get("duration") or "")]},
                materials=mats, measurements=meas, conclusions=concl,
                snippet=equote[:600], locator_kind=loc.locator_kind,
                locator=loc.locator))

            # авто-lineage: продукт derived_from вход (в рамках эксперимента)
            outs = [m for m in mats if m.role == "output"]
            ins = [m for m in mats if m.role == "input"]
            for o in outs:
                for i in ins:
                    batch.lineage.append(BatchSemanticEdge(
                        src=o.material_id, dst=i.material_id,
                        process=entities.canonical_process(raw.get("process") or ""),
                        snippet=equote[:400], doc_id=doc_id))
    return batch


def _extract_from_doc(ex: Extractor, doc, doc_path: Path, max_chunks: int,
                      workers: int, okf_raw_path: str | None) -> ExtractionBatch:
    lang = detect_lang(doc.full_text)
    chunks = doc.chunks(max_chars=6000)[:max_chunks]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda blocks: ex.extract_chunk("\n".join(b.text for b in blocks)),
            chunks))
    name = ("nuextract_v3" if "NuExtract" in ex.model
            else "mock_rule" if ex.model.startswith("mock") else "llm_v1")
    return assemble_batch(doc_path, list(zip(chunks, results)), lang,
                          doc.artifact_sha256, name, okf_raw_path)


def extract_document(ex: Extractor, path: Path, max_chunks: int = 8,
                     workers: int = 4,
                     okf_raw_path: str | None = None) -> ExtractionBatch:
    return _extract_from_doc(ex, parse_document(path), path,
                             max_chunks, workers, okf_raw_path)


def extract_markdown_text(ex: Extractor, text: str, source_path: str,
                          okf_raw_path: str | None = None, max_chunks: int = 8,
                          workers: int = 4) -> ExtractionBatch:
    """Экстракция из markdown-текста (без файла) — для HTTP-ингеста OKF из
    parser SHARED. source_path = сырой путь SHARED (для title/slug),
    okf_raw_path = ключ для wiki-диплинка."""
    from .parse import parse_markdown_text
    return _extract_from_doc(ex, parse_markdown_text(text, source_path),
                             Path(source_path), max_chunks, workers, okf_raw_path)


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dir", type=Path, help="взять все .docx/.docm/.pdf из папки")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--max-chunks", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--doc-workers", type=int, default=3, help="документов параллельно")
    ap.add_argument("--load", action="store_true", help="грузить в Postgres")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    files = list(args.files)
    if args.dir:
        files += sorted(p for p in args.dir.iterdir()
                        if p.suffix.lower() in (".docx", ".docm", ".pdf"))
    files = files[:args.limit]
    if not files:
        print("нет входных файлов"); return

    if args.model == "mock":
        from .mock import MockExtractor
        ex: Extractor | "MockExtractor" = MockExtractor()
    else:
        ex = Extractor(model=args.model)
    print(f"модель: {ex.model} · документов: {len(files)}")
    ex.warmup()
    BATCH_DIR.mkdir(exist_ok=True)

    def one(path: Path) -> tuple[Path, ExtractionBatch | None, str]:
        try:
            b = extract_document(ex, path, max_chunks=args.max_chunks)
            out = BATCH_DIR / f"{slugify(path.stem)}.json"
            out.write_text(b.model_dump_json(indent=1), encoding="utf-8")
            return path, b, ""
        except Exception as e:
            return path, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=args.doc_workers) as pool:
        done = list(pool.map(one, files))

    total = {"docs": 0, "experiments": 0, "measurements": 0, "conclusions": 0,
             "materials": 0, "lineage": 0, "errors": 0}
    for path, b, err in done:
        if b is None:
            total["errors"] += 1
            print(f"  FAIL {path.name[:60]}: {err[:120]}")
            continue
        n_m = sum(len(e.measurements) for e in b.experiments)
        n_c = sum(len(e.conclusions) for e in b.experiments)
        total["docs"] += 1
        total["experiments"] += len(b.experiments)
        total["measurements"] += n_m
        total["conclusions"] += n_c
        total["materials"] += len(b.materials)
        total["lineage"] += len(b.lineage)
        print(f"  ok {path.name[:56]:<58} exp={len(b.experiments):>2} "
              f"meas={n_m:>3} concl={n_c:>2} mat={len(b.materials):>2}")
    print("итого:", json.dumps(total, ensure_ascii=False))

    if args.load:
        from ..loader import load_batch, seed_registries
        from ..store import Store
        store = Store.open(args.db)
        seed_registries(store)
        agg = {}
        for path, b, err in done:
            if b is None:
                continue
            rep = load_batch(store, b)
            for k, v in rep.counts.items():
                agg[k] = agg.get(k, 0) + v
        print("в БД:", json.dumps(agg, ensure_ascii=False))
        store.close()


if __name__ == "__main__":
    main()
