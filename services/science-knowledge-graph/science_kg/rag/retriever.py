"""Graph retriever for RAG: question → relevant subgraph from Neo4j."""

from science_kg.embeddings import embed_text
from science_kg.graph.neo4j_client import Neo4jClient
from science_kg.models import GraphNode, GraphEdge, RetrievalContext
from science_kg.nlp.normalizer import canonical_material
from science_kg.nlp.pipeline import detect_language, get_nlp_for_text

_VECTOR_SEARCH_K = 10
_MAX_SOURCE_TEXTS = 4  # raw chunks fed to the LLM alongside graph triples —
# capped to keep the prompt within budget (chunks are ~20K chars each)
_MAX_SOURCE_TEXT_CHARS = 25_000  # hard cap per raw source text; full .md files
# loaded by load_precomputed_facts.py can be megabytes, and gpt-4o-mini rejects
# prompts that exceed its context window.


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
        for term in terms:
            for node in await self._find_nodes(term):
                anchors[node.text] = node
                contains_matched.add(node.text)
                term_docs.setdefault(term, set()).update(node.sources)

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

        # Rank source documents by how many query terms they cover, then by
        # the rarity (specificity) of the anchors that point to them. This puts
        # the article that is actually about the whole question first.
        doc_terms: dict[str, set[str]] = {}
        for term, docs in term_docs.items():
            for doc_id in docs:
                doc_terms.setdefault(doc_id, set()).add(term)

        def _doc_specificity(doc_id: str) -> int:
            return min(
                (len(n.sources) for n in anchors.values() if doc_id in n.sources),
                default=0,
            )

        ranked_doc_ids = sorted(
            doc_terms.keys(),
            key=lambda d: (-len(doc_terms[d]), _doc_specificity(d)),
        )

        # Fetch raw prose for the top few most-relevant chunks so the LLM can
        # answer narrative questions the entity/relation triples don't capture.
        top_doc_ids = ranked_doc_ids[:_MAX_SOURCE_TEXTS]
        texts_by_id = await self._client.get_document_texts(top_doc_ids)
        source_texts = [
            _truncate_text(texts_by_id[d], _MAX_SOURCE_TEXT_CHARS)
            for d in top_doc_ids
            if d in texts_by_id
        ]

        # Sources in RELEVANCE order (highest-coverage anchors' documents
        # first), not alphabetical — the UI shows the top few as citation
        # links, so the actually-answering document must lead. Leftover
        # neighbourhood sources follow, sorted for stability.
        ordered_sources = list(ranked_doc_ids)
        ordered_sources += sorted(all_sources - set(ranked_doc_ids))

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
