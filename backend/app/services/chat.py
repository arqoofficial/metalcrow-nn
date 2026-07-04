"""Обработка одного сообщения чата (SPEC_V3 §5.7/§8.4).

TODO(SPEC_V3 §5.7 P1/P2): полноценный LLM-агент с tool-calling loop, structured
claims validator (fact — strict, hypothesis — soft), degraded-mode retry.
Обычная (не gap_click) ветка вызывает два tool'а: `hybrid_search` (Postgres,
даёт `experiment_ids`-доказательства) и `science-knowledge-graph`'s `/rag/query`
(GraphRAG — LLM-ответ, заземлённый на graph-контекст). Если
science-knowledge-graph недоступен или не ответил — summary остаётся шаблонной
строкой, как раньше (та же деградация, что и у `generate_hypothesis`, см.
`services/agent`).

Пользователь может явно выбрать источник знаний через `request.metadata.mode`
(`ChatMode`): `ontology` жёстко использует только typed-онтологию (Postgres,
provenance-цитаты), `knowledge_graph` — только Neo4j GraphRAG + hybrid_search,
`auto` (по умолчанию) — прежний приоритетный waterfall ontology → knowledge_graph.
Фактически сработавший источник возвращается в `ChatMessageResponse.mode_used`,
чтобы фронтенд мог показать это явно, а не только через `tools_used`.
"""

import re
import uuid

from sqlmodel import Session, col, select

from app.models.chat import ChatMessage, ChatRole, ChatSession
from app.schemas.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    ChatMode,
    ChatSource,
    ChatTrigger,
    Claim,
    ClaimConfidence,
    ClaimKind,
)
from app.schemas.search import SearchRequest
from app.services import agent, ontology_client, science_kg_client, wiki

# ontology-knowledge-graph kind → как показываем в чате
_ONTO_CONF = {"high": ClaimConfidence.HIGH, "medium": ClaimConfidence.MEDIUM,
              "low": ClaimConfidence.LOW}

_MAX_RESOLVED_SOURCES = 3  # cap sources surfaced per answer — rag_result
# ['sources'] is relevance-ordered, so the top few are the documents that
# actually answered; showing every touched neighbourhood source (on a small,
# densely-connected corpus that can be the whole set) just adds noise.

# doc_ids from SHARED-ingested articles are "<raw_path>::chunk{i}" (see
# science-knowledge-graph scripts/ingest_shared_corpus.py).
_CHUNK_MARKER = "::chunk"
_OKF_STAGE1_PREFIX = "01_docling_clean00/"
# Parser tree API may flatten subdirs (list as RAW_DATA/file.pdf while the
# concrete raw file and OKF markdown live under RAW_DATA/Доклады/file.pdf).
_RAW_DATA_SUBFOLDERS = ("Доклады", "Обзоры", "Журналы")


def _shared_raw_path_candidates(source_path: str) -> list[str]:
    """Candidate SHARED raw paths for one ingested doc_id prefix."""
    candidates: list[str] = []
    if source_path.startswith("RAW_DATA/") and source_path.count("/") == 1:
        name = source_path.split("/", 1)[1]
        for subdir in _RAW_DATA_SUBFOLDERS:
            candidates.append(f"RAW_DATA/{subdir}/{name}")
    candidates.append(source_path)
    seen: set[str] = set()
    out: list[str] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _okf_path_for_source_path(source_path: str) -> str | None:
    """Resolve a wiki deep-link okf_path, probing parser-backed wiki content."""
    for raw in _shared_raw_path_candidates(source_path):
        okf_path = f"{_OKF_STAGE1_PREFIX}{raw}.md"
        if wiki.get_document_content(okf_path) is not None:
            return okf_path
    return None


def _okf_path_for_filename(filename: str) -> str | None:
    """Resolve okf_path when Neo4j/RAG only has a bare filename (precomputed
    facts corpus) rather than a full ``RAW_DATA/...::chunk{i}`` doc_id."""
    name = filename.rsplit("/", 1)[-1]
    if "/" in filename or "." not in name:
        return None
    query = name.removesuffix(".pdf") if name.lower().endswith(".pdf") else name
    for item in wiki.search_documents(query, limit=20).results:
        try:
            raw = wiki.okf_to_raw_path(item.okf_path)
        except ValueError:
            continue
        if raw.rsplit("/", 1)[-1].lower() == name.lower():
            return item.okf_path
    return None


def _resolve_one_chat_source(ref: str) -> tuple[str | None, str | None, str | None]:
    """Map one RAG source reference to (source_path, okf_path, filename)."""
    source_ref = ref.split(_CHUNK_MARKER, 1)[0]
    filename = source_ref.rsplit("/", 1)[-1]
    okf_path = _okf_path_for_source_path(source_ref)
    if okf_path is None and "/" not in source_ref:
        okf_path = _okf_path_for_filename(source_ref)
    source_path = source_ref if "/" in source_ref else None
    if okf_path:
        source_path = wiki.okf_to_raw_path(okf_path)
        filename = source_path.rsplit("/", 1)[-1]
    return source_path, okf_path, filename


def _resolve_chat_sources(doc_ids: list[str]) -> list[ChatSource]:
    """rag_result['sources'] is a relevance-ordered, deduplicated doc_id list
    (see science_kg/rag/retriever.py GraphRetriever.retrieve). Several doc_ids
    can be different chunks of the SAME article, so dedupe on the parsed
    source_path, preserving relevance order, and cap the list.

    doc_ids not shaped as "<raw_path>::chunk{i}" (e.g. legacy corpus without a
    SHARED source path) still surface as a chip labelled by doc_id, just
    without a wiki deep-link."""
    seen_keys: set[str] = set()
    out: list[ChatSource] = []
    for doc_id in doc_ids:
        source_path, okf_path, filename = _resolve_one_chat_source(doc_id)
        dedupe_key = source_path or filename or doc_id
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        out.append(
            ChatSource(
                doc_id=doc_id,
                filename=filename,
                source_path=source_path,
                okf_path=okf_path,
            )
        )
        if len(out) >= _MAX_RESOLVED_SOURCES:
            break
    return out


def refresh_stored_message_sources(metadata: dict | None) -> dict | None:
    """Re-resolve wiki links for persisted assistant metadata.

    Older answers stored ``okf_path=null`` when RAG returned bare filenames;
    refresh on read so history chips become clickable without re-asking."""
    if not metadata:
        return metadata
    claims = metadata.get("claims")
    if not isinstance(claims, list):
        return metadata
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        sources = claim.get("sources")
        if not isinstance(sources, list) or not sources:
            continue
        if all(isinstance(s, dict) and s.get("okf_path") for s in sources):
            continue
        doc_ids = [
            s.get("doc_id")
            for s in sources
            if isinstance(s, dict) and s.get("doc_id")
        ]
        if not doc_ids:
            continue
        claim["sources"] = [
            s.model_dump(mode="json") for s in _resolve_chat_sources(doc_ids)
        ]
    return metadata


def _ontology_claims(question: str) -> tuple[list[Claim], list[str]]:
    """Спросить онтологию; пустой список = не нашла (деградация на старый
    контур). Цитаты-провенанс вшиваются в текст claim'а (schema Claim не несёт
    отдельного поля citations)."""
    result = ontology_client.ask(question)
    if not result:
        return [], []
    answer_text = result.get("answer")
    no_answer = bool(result.get("no_answer"))
    raw_claims = result.get("claims") or []
    if not raw_claims and not answer_text:
        return [], []
    tools_used_raw = result.get("tools_used") or []
    tool_args = result.get("tool_args") or {}
    # Пустой интент или evidence без слотов — не перехватываем чат.
    if not tools_used_raw:
        return [], []
    if tools_used_raw == ["evidence"] and not tool_args:
        return [], []
    claims: list[Claim] = []
    # Ведущий claim = синтезированный LLM-ответ (generation поверх пассажей); он
    # же становится summary. Если синтез не нашёл ответа — честная строка, но
    # найденные пассажи всё равно показываем как доказательства с провенансом.
    if answer_text:
        claims.append(Claim(
            text=answer_text, experiment_ids=[],
            confidence=ClaimConfidence.MEDIUM, kind=ClaimKind.FACT,
        ))
    elif no_answer:
        claims.append(Claim(
            text="В корпусе не нашлось прямого ответа на этот вопрос; "
                 "ниже — связанные фрагменты.",
            experiment_ids=[], confidence=ClaimConfidence.LOW, kind=ClaimKind.FACT,
        ))
    for c in raw_claims[:8]:
        text = c.get("text", "")
        cites = [s for s in c.get("citations", []) if s]
        if cites:
            text += f"\n— источник: «{cites[0][:180]}»"
        kind_label = c.get("kind", "fact")
        if kind_label not in ("fact",):
            text = f"[{kind_label}] {text}"
        claims.append(Claim(
            text=text,
            experiment_ids=[],
            confidence=_ONTO_CONF.get(c.get("confidence") or "", ClaimConfidence.MEDIUM),
            kind=ClaimKind.FACT,
        ))
    tools = [f"ontology:{t}" for t in result.get("tools_used", [])]
    return claims, tools


def _knowledge_graph_answer(
    session: Session, request: ChatMessageRequest
) -> tuple[Claim, list[str], str]:
    """Ветка knowledge_graph: Postgres `hybrid_search` + science-knowledge-graph
    GraphRAG. Используется и как явный режим, и как fallback в auto."""
    search_response = agent.hybrid_search(
        session, SearchRequest(query=request.content, top_k=5)
    )
    experiment_ids = [item.experiment_id for item in search_response.results]
    tools_used = ["hybrid_search"]

    # generate_answer() (science-knowledge-graph) now always calls the LLM and
    # decides itself whether the message is casual conversation or a domain
    # question with/without graph data — its `answer` is always a real,
    # context-appropriate reply, so no need to pre-filter on matched_entities
    # here (that used to be needed only to avoid surfacing science-kg's old
    # hardcoded "no data" string on plain greetings).
    rag_result = science_kg_client.rag_query(request.content)
    if rag_result and rag_result.get("answer"):
        tools_used.append("graph_rag_query")
        summary = rag_result["answer"]
        raw_sources = rag_result.get("sources") or []
        resolved_sources = _resolve_chat_sources(raw_sources)
        has_evidence = bool(experiment_ids or raw_sources)
        claim = Claim(
            text=summary,
            experiment_ids=experiment_ids,
            confidence=ClaimConfidence.MEDIUM if has_evidence else ClaimConfidence.LOW,
            kind=ClaimKind.FACT,
            sources=resolved_sources,
        )
    elif experiment_ids:
        summary = (
            f"Найдено {search_response.total} релевантных экспериментов, "
            f"топ-{len(experiment_ids)} использованы как доказательства."
        )
        claim = Claim(
            text=summary,
            experiment_ids=experiment_ids,
            confidence=ClaimConfidence.MEDIUM,
            kind=ClaimKind.FACT,
        )
    else:
        summary = "По запросу ничего не найдено в корпусе."
        claim = Claim(
            text=summary,
            experiment_ids=[],
            confidence=ClaimConfidence.LOW,
            kind=ClaimKind.FACT,
        )
    return claim, tools_used, summary


_SESSION_TITLE_MAX_LEN = 60


def _title_from_first_message(
    content: str, *, max_len: int = _SESSION_TITLE_MAX_LEN
) -> str:
    """Первое сообщение пользователя → заголовок для сайдбара."""
    collapsed = re.sub(r"\s+", " ", content.strip())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


def _maybe_autotitle_session(
    session: Session, chat_session_id: uuid.UUID, first_message: str
) -> None:
    """Если у сессии нет названия — подставить усечённое первое сообщение."""
    chat_session = session.get(ChatSession, chat_session_id)
    if chat_session is None or (chat_session.title or "").strip():
        return
    has_messages = session.exec(
        select(ChatMessage.id)
        .where(ChatMessage.session_id == chat_session_id)
        .limit(1)
    ).first()
    if has_messages is not None:
        return
    chat_session.title = _title_from_first_message(first_message)
    session.add(chat_session)


def find_reusable_empty_session(
    session: Session, user_id: uuid.UUID
) -> ChatSession | None:
    """Последняя сессия без названия и сообщений — не плодить дубликаты."""
    latest = session.exec(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(col(ChatSession.created_at).desc())
        .limit(1)
    ).first()
    if latest is None or (latest.title or "").strip():
        return None
    has_messages = session.exec(
        select(ChatMessage.id)
        .where(ChatMessage.session_id == latest.id)
        .limit(1)
    ).first()
    if has_messages is not None:
        return None
    return latest


def delete_session(session: Session, chat_session: ChatSession) -> None:
    """Удалить сессию чата; сообщения удаляются каскадно на уровне БД
    (FK `chat_message.session_id` объявлен с `ondelete="CASCADE"`)."""
    session.delete(chat_session)
    session.commit()


def answer_message(
    session: Session, chat_session_id: uuid.UUID, request: ChatMessageRequest
) -> ChatMessageResponse:
    _maybe_autotitle_session(session, chat_session_id, request.content)
    session.add(
        ChatMessage(
            session_id=chat_session_id,
            role=ChatRole.USER,
            content=request.content,
            message_metadata=(
                request.metadata.model_dump(mode="json") if request.metadata else None
            ),
        )
    )

    is_gap_click = bool(
        request.metadata
        and request.metadata.trigger == ChatTrigger.GAP_CLICK
        and request.metadata.gap_cell
    )
    mode = request.metadata.mode if request.metadata else ChatMode.AUTO

    onto_claims: list[Claim] = []
    if is_gap_click:
        assert request.metadata is not None and request.metadata.gap_cell is not None
        claim = agent.generate_hypothesis(request.metadata.gap_cell)
        tools_used = ["generate_hypothesis"]
        summary = claim.text
        mode_used = "hypothesis"
    elif mode == ChatMode.KNOWLEDGE_GRAPH:
        # Явный выбор пользователя — онтология не спрашивается вовсе.
        claim, tools_used, summary = _knowledge_graph_answer(session, request)
        mode_used = "knowledge_graph"
    elif mode == ChatMode.ONTOLOGY:
        # Явный выбор пользователя — knowledge_graph не используется, даже
        # для обогащения experiment_ids, чтобы ответ был честно только из
        # онтологии.
        mode_used = "ontology"
        onto = _ontology_claims(request.content)
        if onto and onto[0]:
            onto_claims, tools_used = onto
            summary = onto_claims[0].text.split("\n")[0]
            if len(onto_claims) > 1:
                summary += f" (+ ещё {len(onto_claims) - 1} утв.)"
            claim = onto_claims[0]
        else:
            tools_used = ["ontology:no_match"]
            summary = (
                "Онтология не нашла структурированного ответа на этот вопрос. "
                "Попробуйте режим «Граф знаний» или переформулируйте запрос."
            )
            claim = Claim(
                text=summary,
                experiment_ids=[],
                confidence=ClaimConfidence.LOW,
                kind=ClaimKind.FACT,
            )
    elif (onto := _ontology_claims(request.content)) and onto[0]:
        # auto: онтология ответила структурно (типизированные факты + дословные
        # цитаты) — её claims идут первыми; hybrid_search добавляет
        # experiment_ids-доказательства из своего индекса.
        onto_claims, tools_used = onto
        search_response = agent.hybrid_search(
            session, SearchRequest(query=request.content, top_k=5)
        )
        if search_response.results:
            tools_used.append("hybrid_search")
            onto_claims[0].experiment_ids = [
                item.experiment_id for item in search_response.results
            ]
        summary = onto_claims[0].text.split("\n")[0]
        if len(onto_claims) > 1:
            summary += f" (+ ещё {len(onto_claims) - 1} утв.)"
        claim = onto_claims[0]
        mode_used = "ontology"
    else:
        # auto: онтология промолчала — fallback на knowledge_graph.
        claim, tools_used, summary = _knowledge_graph_answer(session, request)
        mode_used = "knowledge_graph"

    response = ChatMessageResponse(
        claims=onto_claims or [claim],
        summary=summary,
        tools_used=tools_used,
        subgraph=None,
        session_id=chat_session_id,
        mode_used=mode_used,
    )

    session.add(
        ChatMessage(
            session_id=chat_session_id,
            role=ChatRole.ASSISTANT,
            content=summary,
            message_metadata=response.model_dump(mode="json"),
        )
    )
    session.commit()
    return response
