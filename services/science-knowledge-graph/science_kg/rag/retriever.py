"""Graph retriever for RAG: question → relevant subgraph from Neo4j."""

from science_kg.embeddings import embed_text
from science_kg.graph.neo4j_client import Neo4jClient
from science_kg.models import GraphNode, GraphEdge, RetrievalContext
from science_kg.nlp.normalizer import canonical_material
from science_kg.nlp.pipeline import detect_language, get_nlp_for_text

_VECTOR_SEARCH_K = 10
# Source excerpts fed to the LLM come from the UNION of three document rankings
# (title-match + specificity-first + coverage-first, see retrieve()) so both
# narrow-topic and multi-term questions get their answering article.
_SOURCE_TEXTS_BY_TITLE = 2  # top documents whose TITLE matches the query
_SOURCE_TEXTS_BY_SPECIFICITY = 3  # top focused-article slots
_SOURCE_TEXTS_BY_COVERAGE = 3  # top whole-question-coverage slots
_MAX_SOURCE_TEXT_CHARS = 13_000  # hard cap per raw source text; up to ~8
# excerpts × 13K ≈ 104K chars keeps the prompt inside gpt-4o-mini's context
# window (full .md files loaded by load_precomputed_facts.py can be megabytes).


def _truncate_text(text: str, max_chars: int) -> str:
    """Return *text* truncated to ~max_chars, preferring a paragraph boundary."""
    if len(text) <= max_chars:
        return text
    # Look for the last paragraph break before the limit.
    cut = text.rfind("\n\n", 0, max_chars)
    if cut == -1:
        cut = text.rfind("\n", 0, max_chars)
    if cut == -1:
        cut = max_chars
    return text[:cut].rstrip() + "\n\n[truncated]"


class GraphRetriever:
    def __init__(
        self, client: Neo4jClient, max_hops: int = 2, max_nodes: int = 50
    ) -> None:
        self._client = client
        self._max_hops = max_hops
        self._max_nodes = max_nodes

    async def _find_nodes(self, term: str) -> list[GraphNode]:
        """Find candidate anchor entities for a query term.

        Exact matches (case-insensitive `n.text == term`) rank first, then
        shortest containing text. Without this ordering a bare
        `CONTAINS`+`LIMIT` returns 5 *arbitrary* substring hits — for a common
        term like "меди" that's junk fragments ("монохлорида меди", "меси
        меди", "Балхашмедь") instead of the actual "меди"/"Cu" entity, and the
        real element node the question is about never becomes an anchor.
        Ordering by exactness then `size(n.text)` surfaces the tight,
        canonical entity ("Se", "меди") ahead of long incidental fragments
        that merely embed the term."""
        cypher = """
        MATCH (n:Entity)
        WHERE toLower(n.text) CONTAINS toLower($term)
        RETURN n,
            CASE WHEN toLower(n.text) = toLower($term) THEN 0 ELSE 1 END AS exactness
        ORDER BY exactness, size(n.text)
        LIMIT 5
        """
        async with self._client._driver.session(
            database=self._client._database
        ) as session:
            result = await session.run(cypher, term=term)
            records = await result.data()
        return [
            GraphNode(
                text=r["n"]["text"],
                type=r["n"]["type"],
                sources=r["n"].get("sources", []),
            )
            for r in records
        ]

    async def retrieve(self, question: str) -> RetrievalContext:
        """
        Find anchor entities via two complementary channels — exact substring
        match (CONTAINS, on extracted terms) and semantic similarity (vector
        search, on the whole question) — then return the union of their 2-hop
        neighbourhoods. CONTAINS gives free precision on exact terms; vector
        search adds recall for paraphrases/synonyms/cross-lingual matches that
        never share a literal substring.

        Anchors ARE ranked before expansion, by (channel, ascending
        len(node.sources)):

        1. CONTAINS-matched anchors first, vector-search-only anchors last.
           CONTAINS anchors are exact matches on the question's own (lemma-
           expanded) nouns, so they're far more likely to actually be what
           the question is about; vector search's whole-question embedding
           can surface semantically-adjacent but unrelated hub nodes (e.g.
           EXPERT/PUBLICATION nodes with coincidentally close embeddings)
           that would otherwise crowd out the real match purely for having
           fewer `sources`.
        2. Then by DOCUMENT term-coverage, descending: an anchor scores by how
           many DISTINCT query terms are matched by anchors sharing one of its
           source documents. This is the "which article is actually about the
           question" signal — for "селен и теллур при электроэкстракции меди"
           the target article's anchors cover 4 terms (селен+теллур+медь+
           электроэкстракция) while an unrelated journal that merely mentions
           selenium covers 1, so the target article's neighbourhood is expanded
           first and its (same_source_only-bounded) context wins the budget.
        3. Finally by rarity (ascending len(node.sources)): among equally-
           covering anchors, expand more specific entities before generic hubs.
           On a big corpus a hub's own 2-hop neighbourhood alone can exceed
           `max_nodes`, and without this the loop's early-break (once
           `max_nodes` is hit) would silently starve every anchor processed
           after it of any context budget at all.
        """
        terms = _expand_search_terms(_extract_terms(question))
        exact_terms, lemma_terms = _classify_query_terms(question)

        anchors: dict[str, GraphNode] = {}
        contains_matched: set[str] = set()
        # term -> set of source docs its anchors point at, for coverage scoring
        term_docs: dict[str, set[str]] = {}
        # (term, doc) -> the MOST specific anchor (fewest sources) that links
        # this query term to this document. Used below for specificity-weighted
        # document ranking; keeping the rarest anchor per (term, doc) is what
        # lets a focused article outrank a hub document on the same term.
        term_doc_anchor: dict[tuple[str, str], GraphNode] = {}
        for term in terms:
            for node in await self._find_nodes(term):
                anchors[node.text] = node
                contains_matched.add(node.text)
                term_docs.setdefault(term, set()).update(node.sources)
                for doc_id in node.sources:
                    key = (term, doc_id)
                    prev = term_doc_anchor.get(key)
                    if prev is None or len(node.sources) < len(prev.sources):
                        term_doc_anchor[key] = node

        query_embedding = await embed_text(question)
        if query_embedding is not None:
            for node in await self._client.vector_search(
                query_embedding, k=_VECTOR_SEARCH_K
            ):
                anchors.setdefault(node.text, node)

        def _coverage(node: GraphNode) -> int:
            """Max number of distinct query terms covered by any single source
            document of this node. A node that appears in a document matching
            many question terms is a far better anchor than a node that merely
            shares *some* source with a broad term's anchor set."""
            docs = set(node.sources)
            if not docs:
                return 0
            return max(
                sum(1 for docs_for_term in term_docs.values() if doc in docs_for_term)
                for doc in docs
            )

        def _anchor_priority(node: GraphNode) -> int:
            """Exact query term > lemmatised/canonical form > substring match."""
            low = node.text.lower()
            if low in exact_terms:
                return 0
            if low in lemma_terms:
                return 1
            return 2

        ordered_anchors = sorted(
            anchors.values(),
            key=lambda n: (
                _anchor_priority(n),
                0 if n.text in contains_matched else 1,
                -_coverage(n),
                len(n.sources),
            ),
        )

        all_nodes: dict[str, GraphNode] = {}
        all_edges: dict[tuple[str, str, str], GraphEdge] = {}
        matched_entities: list[str] = list(anchors.keys())
        all_sources: set[str] = set()

        for anchor in ordered_anchors:
            result = await self._client.get_entity_neighbourhood(
                anchor.text, depth=self._max_hops, same_source_only=True
            )
            for node in result.nodes:
                all_nodes[node.text] = node
                all_sources.update(node.sources)
            for edge in result.edges:
                key = (edge.source, edge.relation, edge.target)
                all_edges[key] = edge
                all_sources.update(edge.sources)

            if len(all_nodes) >= self._max_nodes:
                break

        # Trim to limit
        nodes = list(all_nodes.values())[: self._max_nodes]
        node_texts = {n.text for n in nodes}
        edges = [
            e
            for e in all_edges.values()
            if e.source in node_texts and e.target in node_texts
        ]

        # Rank source documents for prose extraction by SPECIFICITY-WEIGHTED
        # term coverage, not a raw coverage count. A giant "hub" document — a
        # conference proceedings that is the source of 6000+ entities — covers
        # almost every query term incidentally, so under a plain coverage count
        # it won the top source-text slots for *every* question. Every answer
        # was then grounded on the same handful of journal cover pages instead
        # of the article that actually discusses the question, and the LLM
        # (correctly) replied "no information in the context". Now each term a
        # document covers contributes 1/len(anchor.sources) — times an
        # exact/lemma-match bonus — so a rare, focused entity (the one an
        # article is really *about*) is worth far more than a generic entity
        # that appears in hundreds of documents. Summing across covered terms
        # still rewards the document that covers the whole question, but hub
        # documents are suppressed because their coverage comes entirely from
        # low-specificity anchors.
        doc_terms: dict[str, set[str]] = {}
        for term, docs in term_docs.items():
            for doc_id in docs:
                doc_terms.setdefault(doc_id, set()).add(term)

        def _anchor_weight(node: GraphNode) -> float:
            # Exact/lemma matches are weighted heavily: a rare entity whose text
            # IS a query term (e.g. the «Мышьяк» entity that lives in essentially
            # one document, df=2) is the single strongest "this article is about
            # the question" signal there is, and must outweigh a document that
            # merely accumulates many weak substring matches.
            bonus = (3.0, 2.0, 1.0)[_anchor_priority(node)]
            return bonus / (len(node.sources) or 1)

        doc_relevance: dict[str, float] = {}
        for (_term, doc_id), node in term_doc_anchor.items():
            doc_relevance[doc_id] = doc_relevance.get(doc_id, 0.0) + _anchor_weight(node)

        ranked_doc_ids = sorted(
            doc_terms.keys(),
            key=lambda d: (-doc_relevance.get(d, 0.0), -len(doc_terms[d])),
        )

        # "Which document answers this" has two shapes, and no single ranking
        # wins both:
        #   • a NARROW-topic question ("самовозгорание сульфидной пыли") is
        #     answered by ONE focused article — surfaced by specificity
        #     (ranked_doc_ids above);
        #   • a MULTI-term question ("селен и теллур при электроэкстракции
        #     меди") is answered by the article covering the WHOLE term set —
        #     surfaced by raw coverage.
        # So the source-text budget is fed the UNION of the two, plus a title
        # channel: documents whose descriptive filename matches the question
        # (some on-topic articles carry no rare distinguishing entity — every
        # term in them is generic — yet the title is a dead giveaway). Either
        # way the former all-slots hub document now takes at most one slot.
        ranked_by_coverage = sorted(
            doc_terms.keys(),
            key=lambda d: (-len(doc_terms[d]), -doc_relevance.get(d, 0.0)),
        )
        title_doc_ids = await self._client.find_documents_by_title(
            terms, limit=_SOURCE_TEXTS_BY_TITLE
        )
        source_doc_ids: list[str] = []
        for doc_id in (
            title_doc_ids
            + ranked_doc_ids[:_SOURCE_TEXTS_BY_SPECIFICITY]
            + ranked_by_coverage[:_SOURCE_TEXTS_BY_COVERAGE]
        ):
            if doc_id not in source_doc_ids:
                source_doc_ids.append(doc_id)

        # Fetch raw prose for these chunks so the LLM can answer narrative
        # questions the entity/relation triples don't capture.
        texts_by_id = await self._client.get_document_texts(source_doc_ids)
        source_texts = [
            _truncate_text(texts_by_id[d], _MAX_SOURCE_TEXT_CHARS)
            for d in source_doc_ids
            if d in texts_by_id
        ]

        # Sources in RELEVANCE order (title matches then highest-coverage
        # anchors' documents first), not alphabetical — the UI shows the top few
        # as citation links, so the actually-answering document must lead.
        # Leftover neighbourhood sources follow, sorted for stability.
        ordered_sources = list(dict.fromkeys(title_doc_ids + ranked_doc_ids))
        ordered_sources += sorted(all_sources - set(ordered_sources))

        return RetrievalContext(
            nodes=nodes,
            edges=edges,
            matched_entities=list(dict.fromkeys(matched_entities)),
            sources=ordered_sources,
            source_texts=source_texts,
        )


# ── Term extraction ───────────────────────────────────────────────────────────

import re

_STOP = frozenset(
    {
        "what",
        "which",
        "how",
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "and",
        "or",
        "by",
        "from",
        "that",
        "this",
        "it",
        "be",
        "affect",
        "effect",
        "property",
        "properties",
        "material",
        "show",
        "have",
        "has",
        "give",
        "get",
        # Russian question words / prepositions / conjunctions — without these,
        # short high-frequency query words leak into _find_nodes and CONTAINS-
        # match unrelated entities ("как" → "Скаков", "при" → "приме…"),
        # flooding the anchor set with cross-document noise. Includes common
        # inflected forms since _extract_terms also feeds lemmas.
        "как",
        "что",
        "какой",
        "какие",
        "какая",
        "чем",
        "где",
        "когда",
        "почему",
        "зачем",
        "который",
        "которые",
        "при",
        "для",
        "под",
        "над",
        "про",
        "без",
        "себя",
        "они",
        "это",
        "этот",
        "эта",
        "все",
        "весь",
        "быть",
        "вести",
        "ведут",
        "влияет",
        "влияние",
        "происходит",
        "свойство",
        "свойства",
        "материал",
        "процесс",
    }
)

_MIN_TERM_LEN = 3


def _expand_search_terms(terms: list[str]) -> list[str]:
    """Add canonical material aliases (e.g. ВТ6 → Ti-6Al-4V) for graph lookup."""
    seen: dict[str, None] = {}
    for term in terms:
        seen[term] = None
        canonical = canonical_material(term)
        if canonical != term:
            seen[canonical] = None
    return list(seen)


def _classify_query_terms(question: str) -> tuple[set[str], set[str]]:
    """Return (exact_terms, lemma_terms) for anchor prioritisation.

    Exact terms appear verbatim in the question; lemma terms are their
    dictionary/nominative forms. Used by `_anchor_priority` so that a node
    labelled exactly «селен» ranks above incidental substrings like
    «селениды» or «населения».
    """
    tokens = re.findall(r"[A-Za-zА-Яа-яёЁ0-9][A-Za-zА-Яа-яёЁ0-9\-\.]*", question)
    exact: set[str] = set()
    lemma: set[str] = set()
    lemmas: dict[str, str] = {}
    if detect_language(question) == "ru":
        doc = get_nlp_for_text(question)(question)
        lemmas = {tok.text: tok.lemma_ for tok in doc}

    for tok in tokens:
        low = tok.lower()
        if len(tok) >= _MIN_TERM_LEN and low not in _STOP:
            exact.add(low)
        lem = lemmas.get(tok)
        if (
            lem
            and lem != tok
            and len(lem) >= _MIN_TERM_LEN
            and lem.lower() not in _STOP
        ):
            lemma.add(lem.lower())
    return exact, lemma


def _extract_terms(question: str) -> list[str]:
    """
    Heuristic term extraction: split on non-alphanumeric, drop stop words,
    keep tokens ≥ 3 chars. Also keep hyphenated tokens (e.g. Ti-6Al-4V).

    For Russian questions, also include each token's lemma (dictionary/
    nominative form): natural questions inflect nouns by case ("меди",
    "селена", "теллура"), but canonical_material's alias map (and
    term_dictionary's synonym_map.json) only has nominative-form surface keys
    ("медь", "селен", "теллур") — without lemmatizing first, _expand_search_terms's
    canonical_material() call never fires for inflected forms, so a question
    like "при электроэкстракции меди" never resolves to the "Cu" entity node.
    Reuses the same cached spaCy pipeline already loaded for document
    ingestion (get_nlp_for_text) instead of adding a separate morphology dep.

    Deduplicated, order-preserving.
    """
    tokens = re.findall(r"[A-Za-zА-Яа-яёЁ0-9][A-Za-zА-Яа-яёЁ0-9\-\.]*", question)

    lemmas: dict[str, str] = {}
    if detect_language(question) == "ru":
        doc = get_nlp_for_text(question)(question)
        lemmas = {tok.text: tok.lemma_ for tok in doc}

    seen: dict[str, None] = {}
    for tok in tokens:
        if len(tok) >= _MIN_TERM_LEN and tok.lower() not in _STOP:
            seen[tok] = None
        lemma = lemmas.get(tok)
        if (
            lemma
            and lemma != tok
            and len(lemma) >= _MIN_TERM_LEN
            and lemma.lower() not in _STOP
        ):
            seen[lemma] = None
    return list(seen)
