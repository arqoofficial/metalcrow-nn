"""Neo4j client — schema bootstrap, writes, and Cypher queries."""

import json
import re

from neo4j import AsyncGraphDatabase, AsyncDriver
from neo4j.exceptions import Neo4jError

from science_kg.models import (
    Entity,
    Relation,
    SearchQuery,
    SearchResult,
    GraphNode,
    GraphEdge,
)
from science_kg.nlp.normalizer import canonical_material, is_element_symbol

_MAX_NEIGHBOURHOOD_DEPTH = 4

# Full-text content channel (SPEC §B1): tokens shorter than this are too generic
# to distinguish documents and only add noise to the BM25 query.
_MIN_CONTENT_TERM_LEN = 4


def _lucene_or_query(terms: list[str]) -> str:
    """Build a Lucene OR query from query terms, sanitised for the fulltext
    parser and biased toward specificity: reserved characters are stripped,
    short/generic tokens dropped, and longer tokens (a proxy for rarer, more
    on-topic vocabulary in Russian technical prose) boosted so the channel
    ranks the article that is *about* the terms over one that merely mentions
    the common ones. Returns "" when nothing usable remains."""
    parts: list[str] = []
    seen: set[str] = set()
    for t in terms:
        cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яёЁ\- ]", " ", t).strip()
        # Chemical element symbols (Cu, Ni, Se…) are shorter than the generic
        # cutoff but are the sharpest content terms — keep them (SPEC §B1).
        too_short = len(cleaned) < _MIN_CONTENT_TERM_LEN and not is_element_symbol(cleaned)
        if too_short or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        token = f'"{cleaned}"' if " " in cleaned else cleaned
        if len(cleaned) >= 7:
            token += "^2"
        parts.append(token)
    return " OR ".join(parts)


class Neo4jClient:
    def __init__(
        self, uri: str, user: str, password: str, database: str = "neo4j"
    ) -> None:
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            uri, auth=(user, password)
        )
        self._database = database

    async def close(self) -> None:
        await self._driver.close()

    async def bootstrap_schema(self) -> None:
        """Create indexes and constraints once on startup."""
        constraints = [
            "CREATE CONSTRAINT entity_unique IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE (e.text, e.type) IS UNIQUE",
        ]
        indexes = [
            "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
            "CREATE INDEX entity_text IF NOT EXISTS FOR (e:Entity) ON (e.text)",
            "CREATE INDEX document_doc_id IF NOT EXISTS FOR (d:Document) ON (d.doc_id)",
            # `e.embedding` is stored as a plain LIST<FLOAT> (via a Cypher list
            # parameter), which vector indexes support on Community Edition —
            # the native VECTOR property type is Enterprise/Aura-only.
            # text-embedding-3-small (science_kg.embeddings), 1536-dim.
            "CREATE VECTOR INDEX entity_embedding_idx IF NOT EXISTS "
            "FOR (e:Entity) ON (e.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: 1536, "
            "`vector.similarity_function`: 'cosine'}}",
            # Full-text (Lucene) index over document prose + title, for the RAG
            # retriever's content channel (SPEC §B1): many on-topic articles
            # carry no rare distinguishing entity and share little vocabulary
            # with the question's phrasing (a "обогатительная фабрика" question
            # vs a "Методы очистки шахтных вод" document), yet the answer text is
            # literally in the body. TF-IDF over the raw text surfaces it when
            # entity/title matching can't.
            "CREATE FULLTEXT INDEX document_text_ft IF NOT EXISTS "
            "FOR (d:Document) ON EACH [d.text, d.doc_id]",
        ]
        async with self._driver.session(database=self._database) as session:
            for stmt in constraints + indexes:
                await session.run(stmt)

    async def upsert_entities(
        self,
        entities: list[Entity],
        embeddings: dict[str, list[float]] | None = None,
    ) -> None:
        """Upsert entities that appear standalone (not covered by upsert_relations).

        `embeddings` (entity text -> vector) is optional and caller-computed
        (science_kg.embeddings.embed_text) — this stays a pure I/O layer, no ML
        logic here. Missing/failed embeddings just leave `e.embedding` unset;
        entity_embedding_idx-based vector_search simply won't surface that node.
        """
        if not entities:
            return
        embeddings = embeddings or {}
        query = """
        UNWIND $rows AS row
        MERGE (e:Entity {text: row.text, type: row.type})
        ON CREATE SET e.sources = [row.source_doc]
        ON MATCH  SET e.sources = CASE
            WHEN row.source_doc IN e.sources THEN e.sources
            ELSE e.sources + row.source_doc
        END
        FOREACH (_ IN CASE WHEN row.embedding IS NOT NULL THEN [1] ELSE [] END |
            SET e.embedding = row.embedding
        )
        """
        rows = [
            {
                "text": e.text,
                "type": e.label.value,
                "source_doc": e.source_doc,
                "embedding": embeddings.get(e.text),
            }
            for e in entities
        ]
        async with self._driver.session(database=self._database) as session:
            await session.run(query, rows=rows)

    async def upsert_relations(
        self,
        relations: list[Relation],
        embeddings: dict[str, list[float]] | None = None,
    ) -> None:
        """
        Write relations using typed relationship labels (:AFFECTS, :PROCESSED_BY, …).

        Cypher does not support parameterised relationship types, so we group
        relations by type and issue one UNWIND query per distinct type — each
        with the type name interpolated as a literal (safe: values come from a
        validated StrEnum, never from raw user input).

        `embeddings` (entity text -> vector) is optional, same as in
        `upsert_entities` — applied to both endpoint nodes of each relation.
        """
        if not relations:
            return

        from collections import defaultdict

        embeddings = embeddings or {}
        by_type: dict[str, list[dict]] = defaultdict(list)
        for rel in relations:
            by_type[rel.relation.value].append(
                {
                    "source": rel.source,
                    "source_type": rel.source_type.value,
                    "target": rel.target,
                    "target_type": rel.target_type.value,
                    "verb": rel.verb,
                    "source_doc": rel.source_doc,
                    "source_embedding": embeddings.get(rel.source),
                    "target_embedding": embeddings.get(rel.target),
                }
            )

        node_merge = """
        UNWIND $rows AS row
        MERGE (src:Entity {text: row.source, type: row.source_type})
        ON CREATE SET src.sources = [row.source_doc]
        ON MATCH  SET src.sources = CASE
            WHEN row.source_doc IN src.sources THEN src.sources
            ELSE src.sources + row.source_doc
        END
        FOREACH (_ IN CASE WHEN row.source_embedding IS NOT NULL THEN [1] ELSE [] END |
            SET src.embedding = row.source_embedding
        )
        MERGE (tgt:Entity {text: row.target, type: row.target_type})
        ON CREATE SET tgt.sources = [row.source_doc]
        ON MATCH  SET tgt.sources = CASE
            WHEN row.source_doc IN tgt.sources THEN tgt.sources
            ELSE tgt.sources + row.source_doc
        END
        FOREACH (_ IN CASE WHEN row.target_embedding IS NOT NULL THEN [1] ELSE [] END |
            SET tgt.embedding = row.target_embedding
        )
        """

        async with self._driver.session(database=self._database) as session:
            for rel_type, rows in by_type.items():
                # Upsert both endpoint nodes first
                await session.run(node_merge, rows=rows)
                # Then create typed relationship — rel_type is a StrEnum value
                edge_query = f"""
                UNWIND $rows AS row
                MATCH (src:Entity {{text: row.source, type: row.source_type}})
                MATCH (tgt:Entity {{text: row.target, type: row.target_type}})
                MERGE (src)-[r:{rel_type}]->(tgt)
                ON CREATE SET r.verb = row.verb, r.sources = [row.source_doc]
                ON MATCH  SET r.sources = CASE
                    WHEN row.source_doc IN r.sources THEN r.sources
                    ELSE r.sources + row.source_doc
                END
                """
                await session.run(edge_query, rows=rows)

    async def search(self, query: SearchQuery) -> SearchResult:
        """
        Return subgraph matching material / regime / property filters.

        Rewritten as a two-step query so the regime filter hits indexed nodes
        directly instead of running a correlated EXISTS subquery per row.

        Step 1 — find anchor nodes (material or regime).
        Step 2 — follow RELATION edges, filter targets by property text.
        """
        cypher = """
        // Step 1: find anchor nodes
        MATCH (anchor:Entity)
        WHERE (
            $material IS NULL OR (anchor.type = 'MATERIAL' AND toLower(anchor.text) CONTAINS toLower($material))
        ) OR (
            $regime IS NULL OR (anchor.type = 'PROCESS' AND toLower(anchor.text) CONTAINS toLower($regime))
        )

        // Step 2: follow edges from anchors (any relationship type)
        WITH anchor
        OPTIONAL MATCH (anchor)-[r]->(target:Entity)
        WHERE $property IS NULL
           OR toLower(target.text) CONTAINS toLower($property)

        // Step 3: if regime filter is set, keep only paths that pass through a regime node
        WITH anchor, r, target
        WHERE $regime IS NULL
           OR anchor.type = 'PROCESS'
           OR EXISTS {
                MATCH (anchor)-[]-(reg:Entity {type: 'PROCESS'})
                WHERE toLower(reg.text) CONTAINS toLower($regime)
              }

        RETURN anchor, r, target, type(r) AS r_type
        LIMIT $limit
        """
        params = {
            "material": (
                canonical_material(query.material) if query.material else None
            ),
            "regime": query.regime,
            "property": query.property_,
            "limit": query.limit,
        }

        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        # NB: `result.data()` hydrates a returned Relationship as a plain
        # (start, type, end) tuple, not a property mapping — `r.get(...)`
        # below would raise AttributeError. Node values are fine (they
        # hydrate to a property dict), so iterate raw Records instead and
        # only pull typed Node/Relationship graph objects out of them.
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, **params)
            records = [record async for record in result]

        for record in records:
            anchor = record["anchor"]
            nodes[anchor["text"]] = GraphNode(
                text=anchor["text"],
                type=anchor["type"],
                sources=anchor.get("sources", []),
            )
            r = record["r"]
            target = record["target"]
            if r is not None and target is not None:
                nodes[target["text"]] = GraphNode(
                    text=target["text"],
                    type=target["type"],
                    sources=target.get("sources", []),
                )
                edges.append(
                    GraphEdge(
                        source=anchor["text"],
                        target=target["text"],
                        relation=record["r_type"],
                        verb=r.get("verb", ""),
                        sources=r.get("sources", []),
                    )
                )

        gaps = _detect_gaps(list(nodes.values()), edges, query)
        return SearchResult(nodes=list(nodes.values()), edges=edges, gaps=gaps)

    async def vector_search(
        self, query_embedding: list[float], k: int = 10, entity_type: str | None = None
    ) -> list[GraphNode]:
        """Semantic-similarity search over `:Entity.embedding` via the
        `entity_embedding_idx` vector index (see `bootstrap_schema`). Finds
        nodes by meaning rather than exact substring — the complement to
        `search()`'s CONTAINS-based matching, not a replacement for it.

        `entity_type` is a post-filter on the vector index's own top-k results
        (Neo4j doesn't support a pre-filtered ANN query here), so it can return
        fewer than `k` nodes when set.
        """
        cypher = """
        CALL db.index.vector.queryNodes('entity_embedding_idx', $k, $embedding)
        YIELD node, score
        WHERE $type IS NULL OR node.type = $type
        RETURN node, score
        ORDER BY score DESC
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                cypher, embedding=query_embedding, k=k, type=entity_type
            )
            records = [record async for record in result]

        return [
            GraphNode(
                text=r["node"]["text"],
                type=r["node"]["type"],
                sources=r["node"].get("sources", []),
            )
            for r in records
        ]

    async def get_entity_neighbourhood(
        self, text: str, depth: int = 2, same_source_only: bool = False
    ) -> SearchResult:
        """
        Return all nodes reachable from a given entity within `depth` hops.

        depth is validated here (not only at the route layer) because this
        value is interpolated into the Cypher query string — Cypher does not
        support parameterised path lengths.

        `same_source_only` (used by the RAG retriever, off for the public
        /entities/{text}/neighbourhood API) keeps only paths where EVERY hop's
        relationship shares a source document with the anchor. On a large,
        densely-connected corpus a plain 2-hop walk leaks across dozens of
        unrelated documents — a common material/process node bridges into
        papers that merely mention the same element — drowning the anchor's
        own article in cross-document noise. Requiring per-hop source overlap
        with the anchor bounds the walk to the handful of documents the anchor
        actually appears in, which is what the RAG answer should be grounded on.
        """
        if not (1 <= depth <= _MAX_NEIGHBOURHOOD_DEPTH):
            raise ValueError(
                f"depth must be between 1 and {_MAX_NEIGHBOURHOOD_DEPTH}, got {depth}"
            )

        # Safe: depth is a validated integer, not user-supplied string
        path_filter = (
            "WHERE all(rel IN r WHERE "
            "any(s IN rel.sources WHERE s IN start.sources))"
            if same_source_only
            else ""
        )
        cypher = f"""
        MATCH (start:Entity {{text: $text}})-[r*1..{depth}]-(neighbour:Entity)
        {path_filter}
        UNWIND r AS rel
        WITH start, neighbour, rel,
             startNode(rel) AS src, endNode(rel) AS tgt
        RETURN DISTINCT
            src.text AS src_text, src.type AS src_type, src.sources AS src_sources,
            tgt.text AS tgt_text, tgt.type AS tgt_type, tgt.sources AS tgt_sources,
            type(rel) AS rel_type, rel.verb AS verb, rel.sources AS rel_sources
        LIMIT 200
        """
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        seen_edges: set[tuple[str, str, str]] = set()

        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, text=text)
            records = await result.data()

        for record in records:
            for txt, typ, srcs in [
                (record["src_text"], record["src_type"], record["src_sources"] or []),
                (record["tgt_text"], record["tgt_type"], record["tgt_sources"] or []),
            ]:
                if txt not in nodes:
                    nodes[txt] = GraphNode(text=txt, type=typ, sources=srcs)
            rel_type = record["rel_type"]
            key = (record["src_text"], rel_type, record["tgt_text"])
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(
                    GraphEdge(
                        source=record["src_text"],
                        target=record["tgt_text"],
                        relation=rel_type,
                        verb=record.get("verb") or "",
                        sources=record.get("rel_sources") or [],
                    )
                )

        return SearchResult(nodes=list(nodes.values()), edges=edges)

    async def delete_isolated_nodes(self) -> int:
        """Delete all Entity nodes that have no relationships. Returns count deleted."""
        cypher = """
        MATCH (n:Entity)
        WHERE NOT (n)--()
        WITH n, n.text AS t
        DELETE n
        RETURN count(*) AS deleted
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher)
            record = await result.single()
            return record["deleted"] if record else 0

    async def list_entities(
        self, entity_type: str | None = None, limit: int = 100
    ) -> list[GraphNode]:
        cypher = """
        MATCH (e:Entity)
        WHERE $type IS NULL OR e.type = $type
        RETURN e
        LIMIT $limit
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, type=entity_type, limit=limit)
            records = await result.data()

        return [
            GraphNode(
                text=r["e"]["text"],
                type=r["e"]["type"],
                sources=r["e"].get("sources", []),
            )
            for r in records
        ]

    async def list_entities_missing_embedding(self, limit: int = 1000) -> list[GraphNode]:
        """For `scripts/backfill_embeddings.py` — nodes upserted before the
        entity_embedding_idx feature existed (or whose embedding computation
        failed at ingest time) have no `embedding` property at all yet."""
        cypher = """
        MATCH (e:Entity)
        WHERE e.embedding IS NULL
        RETURN e
        LIMIT $limit
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, limit=limit)
            records = await result.data()

        return [
            GraphNode(
                text=r["e"]["text"],
                type=r["e"]["type"],
                sources=r["e"].get("sources", []),
            )
            for r in records
        ]

    async def set_embedding(self, text: str, entity_type: str, embedding: list[float]) -> None:
        """Set `embedding` on one specific node — unlike upsert_entities/
        upsert_relations, doesn't touch `sources` (this is a pure backfill of a
        missing property on an already-ingested node, not a re-ingest)."""
        cypher = """
        MATCH (e:Entity {text: $text, type: $type})
        SET e.embedding = $embedding
        """
        async with self._driver.session(database=self._database) as session:
            await session.run(cypher, text=text, type=entity_type, embedding=embedding)

    async def set_embeddings_batch(
        self, items: list[tuple[str, str, list[float]]]
    ) -> None:
        """Batch-set embeddings on existing Entity nodes keyed by (text, type).

        Used by bulk loaders that first create the graph structure without
        embeddings (fast path) and then backfill vectors in one pass.
        """
        if not items:
            return
        rows = [
            {"text": text, "type": entity_type, "embedding": embedding}
            for text, entity_type, embedding in items
        ]
        cypher = """
        UNWIND $rows AS row
        MATCH (e:Entity {text: row.text, type: row.type})
        SET e.embedding = row.embedding
        """
        async with self._driver.session(database=self._database) as session:
            await session.run(cypher, rows=rows)

    async def upsert_document(self, doc_id: str, text: str, meta: dict) -> None:
        """Persist the raw text + metadata of an ingested document under its
        own `:Document` node, keyed by `doc_id` — separate from `:Entity
        {type: PUBLICATION}` (which only ever stores `text = doc_id`, see
        nlp/extractor.py::_add_publication_edges). Needed so a RAG answer's
        `sources` (doc-id strings) can be resolved back to something viewable
        (GET /documents/{doc_id})."""
        cypher = """
        MERGE (d:Document {doc_id: $doc_id})
        SET d.text = $text, d.meta_json = $meta_json
        """
        async with self._driver.session(database=self._database) as session:
            await session.run(
                cypher,
                doc_id=doc_id,
                text=text,
                meta_json=json.dumps(meta, ensure_ascii=False),
            )

    async def get_document(self, doc_id: str) -> dict | None:
        cypher = """
        MATCH (d:Document {doc_id: $doc_id})
        RETURN d.text AS text, d.meta_json AS meta_json
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, doc_id=doc_id)
            record = await result.single()
        if record is None:
            return None
        meta_json = record["meta_json"]
        return {
            "doc_id": doc_id,
            "text": record["text"],
            "meta": json.loads(meta_json) if meta_json else {},
        }

    async def get_document_texts(self, doc_ids: list[str]) -> dict[str, str]:
        """Batch-fetch Document.text for the given doc_ids (order not
        guaranteed — caller re-orders). Used by the RAG retriever to feed the
        LLM the actual chunk prose behind the top-ranked graph anchors, not
        just entity/relation triples."""
        if not doc_ids:
            return {}
        cypher = """
        MATCH (d:Document)
        WHERE d.doc_id IN $doc_ids
        RETURN d.doc_id AS doc_id, d.text AS text
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, doc_ids=doc_ids)
            records = await result.data()
        return {r["doc_id"]: r["text"] for r in records if r["text"]}

    async def find_documents_by_title(
        self, terms: list[str], limit: int = 3
    ) -> list[str]:
        """Return doc_ids whose *filename/title* matches ≥2 distinct query terms.

        The corpus doc_ids are descriptive source paths
        ("RAW_DATA/Обзоры/…Извлечение благородных металлов из шламов…pdf::chunk0"),
        so a document whose title matches the question is almost always
        on-topic — even when its extracted entities are too generic to rank
        (terms like "благородные металлы" or "шлам" appear in hundreds of
        documents and carry no specificity signal).

        A ≥2-term threshold is essential: these titles lead the source-text
        budget, so a single incidental word match would hijack the top slot —
        e.g. a Ni-current-density question must NOT pull "…селена и теллура при
        электроэкстракции меди" just because both mention «электроэкстракции».
        With only one meaningful query term the threshold relaxes to 1 (nothing
        to disambiguate on). Ranked by terms matched, then shortest text (prefer
        the focused chunk over a giant merged file)."""
        min_match = 2 if len(terms) >= 2 else 1
        if not terms:
            return []
        cypher = """
        MATCH (d:Document)
        WITH d, [t IN $terms WHERE toLower(d.doc_id) CONTAINS toLower(t)] AS hits
        WHERE size(hits) >= $min_match
        RETURN d.doc_id AS doc_id, size(hits) AS matched, size(d.text) AS tlen
        ORDER BY matched DESC, tlen ASC
        LIMIT $limit
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                cypher, terms=terms, min_match=min_match, limit=limit
            )
            records = await result.data()
        return [r["doc_id"] for r in records]

    async def find_documents_by_content(
        self, terms: list[str], limit: int = 3
    ) -> list[str]:
        """Return doc_ids whose PROSE best matches the query terms (Lucene BM25
        via the `document_text_ft` full-text index) — the recall backstop of
        SPEC §B1.

        A document can answer a question without sharing a rare entity or a
        title word with it: the relevant sentences just sit in the body
        ("обессоливание", "сульфаты", "осмос" for a water question phrased about
        an «обогатительная фабрика»). BM25 already down-weights terms that occur
        in many documents (IDF), so a generic doc no longer wins purely on term
        count. We additionally drop short/generic tokens and boost longer terms,
        which in Russian technical prose are a decent proxy for specificity, to
        keep the channel precise rather than flooding it with common-word hits.

        Best-effort: if the index is missing (fresh DB before bootstrap) or the
        query fails to parse, return [] and let the other channels stand."""
        lucene = _lucene_or_query(terms)
        if not lucene:
            return []
        cypher = """
        CALL db.index.fulltext.queryNodes('document_text_ft', $q)
        YIELD node, score
        RETURN node.doc_id AS doc_id, score
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            async with self._driver.session(database=self._database) as session:
                result = await session.run(cypher, q=lucene, limit=limit)
                records = await result.data()
        except Neo4jError:
            return []
        return [r["doc_id"] for r in records]


def _detect_gaps(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    query: SearchQuery,
) -> list[str]:
    gaps: list[str] = []
    material_nodes = {n.text for n in nodes if n.type == "MATERIAL"}
    connected_materials = {e.source for e in edges if e.relation == "produces_output"}

    for mat in material_nodes:
        if mat not in connected_materials:
            gaps.append(f"Нет данных об эффектах для материала «{mat}»")

    if query.regime and not any(e.relation == "uses_material" for e in edges):
        gaps.append(f"Режим «{query.regime}» не связан ни с одним материалом")

    return gaps
