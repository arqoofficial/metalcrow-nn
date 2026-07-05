"""API routes — document ingestion, search, graph exploration."""

import asyncio
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form

from science_kg.config import settings
from science_kg.models import (
    Document,
    DocumentDetail,
    ExtractionResult,
    SearchQuery,
    SearchResult,
    GraphNode,
    PDFIngestionResult,
    RAGQuery,
    RAGResponse,
    RetrievalOutcome,
    AnswerStatus,
)
from science_kg.rag.retriever import GraphRetriever
from science_kg.rag.generator import generate_answer, gap_hint
from science_kg.nlp.extractor import process_document
from science_kg.nlp.pdf_extractor import extract_text_from_pdf
from science_kg.nlp.pipeline import get_nlp_for_text, detect_language
from science_kg.embeddings import embed_text

router = APIRouter(prefix="/api/v1")

_MAX_PDF_BYTES = settings.max_pdf_size_mb * 1024 * 1024


def _graph(request: Request):
    return request.app.state.graph


def _langfuse_headers(request: Request) -> dict[str, str]:
    """Forward any `langfuse_*` header the caller (backend) set — LiteLLM at the
    gateway strips the prefix and maps these onto the trace (user id / session
    id). See docs.litellm.ai/docs/observability/langfuse_integration."""
    return {
        k: v
        for k, v in request.headers.items()
        if k.lower().startswith("langfuse_")
    }


def _run_nlp_on_text(text: str, doc_id: str) -> ExtractionResult:
    """CPU-bound: detect language, load model, process. Runs in thread pool."""
    nlp = get_nlp_for_text(text)
    spacy_doc = nlp(text)
    return process_document(spacy_doc, doc_id)


def _run_nlp_batch(items: list[tuple[str, str]]) -> list[ExtractionResult]:
    """
    Batch NLP: groups docs by language, runs nlp.pipe per group.
    items: list of (text, doc_id)
    """
    from science_kg.nlp.pipeline import get_nlp
    from science_kg.config import settings

    ru_items = [(t, d) for t, d in items if detect_language(t) == "ru"]
    en_items = [(t, d) for t, d in items if detect_language(t) == "en"]

    results: dict[str, ExtractionResult] = {}

    for lang_items, model in [
        (ru_items, settings.spacy_model_ru),
        (en_items, settings.spacy_model_en),
    ]:
        if not lang_items:
            continue
        nlp = get_nlp(model)
        texts, doc_ids = zip(*lang_items)
        for spacy_doc, doc_id in zip(nlp.pipe(texts, batch_size=16), doc_ids):
            results[doc_id] = process_document(spacy_doc, doc_id)

    # preserve original order
    return [results[doc_id] for _, doc_id in items]


async def _compute_embeddings(result: ExtractionResult) -> dict[str, list[float]]:
    """One embedding call per unique entity/relation-endpoint text in this
    extraction result. Best-effort: entities whose embedding fails (or when
    OPENAI_API_KEY isn't configured) just don't get one — upsert_entities/
    upsert_relations already treat a missing embedding as optional."""
    texts = {e.text for e in result.entities}
    texts |= {r.source for r in result.relations}
    texts |= {r.target for r in result.relations}
    if not texts:
        return {}

    texts_list = list(texts)
    vectors = await asyncio.gather(*(embed_text(t) for t in texts_list))
    return {
        text: vec for text, vec in zip(texts_list, vectors) if vec is not None
    }


# ── Ingestion ────────────────────────────────────────────────────────────────


@router.post("/documents", response_model=ExtractionResult, status_code=201)
async def ingest_document(doc: Document, request: Request):
    """Parse a document, extract entities and relations, persist to graph."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run_nlp_on_text, doc.text, doc.doc_id)

    embeddings = await _compute_embeddings(result)
    graph = _graph(request)
    await graph.upsert_entities(result.entities, embeddings=embeddings)
    await graph.upsert_relations(result.relations, embeddings=embeddings)
    await graph.upsert_document(doc.doc_id, doc.text, doc.meta)
    return result


@router.post("/documents/batch", response_model=list[ExtractionResult], status_code=201)
async def ingest_batch(docs: list[Document], request: Request):
    """Ingest multiple documents grouped by language for efficient nlp.pipe."""
    items = [(d.text, d.doc_id) for d in docs]

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, partial(_run_nlp_batch, items))

    graph = _graph(request)
    docs_by_id = {d.doc_id: d for d in docs}
    for result in results:
        embeddings = await _compute_embeddings(result)
        await graph.upsert_entities(result.entities, embeddings=embeddings)
        await graph.upsert_relations(result.relations, embeddings=embeddings)
        doc = docs_by_id[result.doc_id]
        await graph.upsert_document(doc.doc_id, doc.text, doc.meta)
    return results


@router.post("/documents/pdf", response_model=PDFIngestionResult, status_code=201)
async def ingest_pdf(
    request: Request,
    file: UploadFile = File(...),
    doc_id: str = Form(None),
):
    """
    Accept a PDF via multipart/form-data.
    Extracts text (including tables), detects language,
    runs NLP pipeline, persists to graph.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()

    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds {settings.max_pdf_size_mb} MB limit",
        )

    loop = asyncio.get_running_loop()

    # PDF parsing + NLP are both CPU-bound — run together in one executor call
    def _process_pdf() -> tuple[ExtractionResult, int, str, str]:
        text, page_count = extract_text_from_pdf(pdf_bytes)
        if not text.strip():
            raise ValueError("Could not extract text from PDF")
        lang = detect_language(text)
        effective_id = doc_id or Path(file.filename).stem
        result = process_document(get_nlp_for_text(text)(text), effective_id)
        return result, page_count, lang, text

    try:
        extraction, page_count, lang, text = await loop.run_in_executor(
            None, _process_pdf
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    embeddings = await _compute_embeddings(extraction)
    graph = _graph(request)
    await graph.upsert_entities(extraction.entities, embeddings=embeddings)
    await graph.upsert_relations(extraction.relations, embeddings=embeddings)
    await graph.upsert_document(
        extraction.doc_id, text, {"filename": file.filename, "language": lang}
    )

    return PDFIngestionResult(
        doc_id=extraction.doc_id,
        filename=file.filename,
        page_count=page_count,
        char_count=sum(len(e.text) for e in extraction.entities),
        language=lang,
        extraction=extraction,
    )


@router.get("/documents/{doc_id:path}", response_model=DocumentDetail)
async def get_document(doc_id: str, request: Request):
    """Fetch the raw text + meta of a previously ingested document — used to
    resolve a `RAGResponse.sources` doc-id into something viewable."""
    doc = await _graph(request).get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetail(**doc)


# ── Search ────────────────────────────────────────────────────────────────────


@router.get("/search", response_model=SearchResult)
async def search(
    request: Request,
    material: str | None = None,
    regime: str | None = None,
    prop: str | None = None,
    limit: int = 20,
):
    """Answer: «что делали со сплавом X при режиме Y — эффект на свойство Z»."""
    if not any([material, regime, prop]):
        raise HTTPException(
            status_code=422, detail="Specify at least one of: material, regime, prop"
        )

    query = SearchQuery(material=material, regime=regime, property=prop, limit=limit)
    return await _graph(request).search(query)


@router.get("/entities/{text}/neighbourhood", response_model=SearchResult)
async def entity_neighbourhood(text: str, request: Request, depth: int = 2):
    """Return the subgraph around a given entity (up to 4 hops)."""
    if depth < 1 or depth > 4:
        raise HTTPException(status_code=422, detail="depth must be between 1 and 4")
    return await _graph(request).get_entity_neighbourhood(text, depth)


# ── Catalogue ─────────────────────────────────────────────────────────────────


@router.get("/entities", response_model=list[GraphNode])
async def list_entities(
    request: Request,
    type: str | None = None,
    limit: int = 100,
):
    """List all known entities, optionally filtered by type."""
    return await _graph(request).list_entities(entity_type=type, limit=limit)


# ── Graph RAG ─────────────────────────────────────────────────────────────────


@router.post("/rag/query", response_model=RAGResponse)
async def rag_query(request: Request, body: RAGQuery):
    """
    Answer a natural-language question using the knowledge graph.

    Retrieves a relevant subgraph from Neo4j and passes it as context
    to Claude to generate a grounded answer with source citations.
    """
    retriever = GraphRetriever(
        _graph(request),
        max_hops=body.max_hops,
        max_nodes=body.max_nodes,
    )
    context = await retriever.retrieve(body.question)
    result = await generate_answer(
        body.question, context, langfuse_headers=_langfuse_headers(request)
    )

    # Grounding flag + honest degradation (SPEC §A2/§A3/§A5), driven by the
    # model's self-reported status instead of a keyword heuristic (which flipped
    # run-to-run on identical context). Only NO_DATA gets the gap hint — the
    # model has told us it is a domain question it couldn't answer; the hint
    # distinguishes "not in corpus" from "couldn't match the query".
    answer = result.answer
    gap_reason: RetrievalOutcome | None = None
    if result.status == AnswerStatus.NO_DATA:
        answer = answer + gap_hint(context)
        gap_reason = (
            RetrievalOutcome.NO_ANCHOR
            if context.outcome == RetrievalOutcome.NO_ANCHOR
            else RetrievalOutcome.WEAK_CONTEXT
        )
    grounded: bool | None = {
        AnswerStatus.GROUNDED: True,
        AnswerStatus.UNGROUNDED: False,
        AnswerStatus.NO_DATA: False,
        AnswerStatus.CASUAL: None,
    }[result.status]

    return RAGResponse(
        answer=answer,
        context_nodes=context.nodes,
        context_edges=context.edges,
        sources=context.sources,
        matched_entities=context.matched_entities,
        grounded=grounded,
        gap_reason=gap_reason,
    )


# ── Health ────────────────────────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok"}
