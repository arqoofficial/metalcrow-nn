from enum import StrEnum
from pydantic import BaseModel, Field


class EntityType(StrEnum):
    MATERIAL = "MATERIAL"
    PROCESS = "PROCESS"  # process name ("закалка") and its condition value
    # ("850°C") share this type, same as REGIME did before the SPEC_V5 rename
    PROPERTY = "PROPERTY"  # absorbs the old VALUE and EFFECT types — both are
    # just a quantified or descriptive characterisation of a property
    EQUIPMENT = "EQUIPMENT"
    EXPERIMENT = "EXPERIMENT"
    PUBLICATION = "PUBLICATION"  # one node per ingested document, see
    # nlp/extractor.py::_add_publication_edges — not extracted via NER/patterns
    EXPERT = "EXPERT"  # was TEAM
    FACILITY = "FACILITY"


class RelationType(StrEnum):
    USES_MATERIAL = "uses_material"  # was PROCESSED_BY
    OPERATES_AT_CONDITION = "operates_at_condition"  # Process(name) <-> Process(condition value)
    PRODUCES_OUTPUT = "produces_output"  # was AFFECTS/PRODUCES
    DESCRIBED_IN = "described_in"  # any entity -> Publication; not grammar-based,
    # see nlp/extractor.py::_add_publication_edges
    VALIDATED_BY = "validated_by"  # was MEASURED_BY
    CONTRADICTS = "contradicts"  # reserved — needs cross-document comparison
    # (Comparability Gate), already implemented properly in
    # services/ontology-knowledge-graph; this extractor doesn't generate it


class Entity(BaseModel):
    text: str
    label: EntityType
    start_char: int
    end_char: int
    source_doc: str = ""


class Relation(BaseModel):
    source: str
    source_type: EntityType
    relation: RelationType
    target: str
    target_type: EntityType
    verb: str = ""
    source_doc: str = ""


class Document(BaseModel):
    doc_id: str
    text: str
    meta: dict = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    doc_id: str
    entities: list[Entity]
    relations: list[Relation]


class DocumentDetail(BaseModel):
    """GET /api/v1/documents/{doc_id} response — raw text + meta of a
    previously ingested document, for resolving RAGResponse.sources into
    something viewable (see Neo4jClient.upsert_document/get_document)."""

    doc_id: str
    text: str
    meta: dict = Field(default_factory=dict)


class SearchQuery(BaseModel):
    material: str | None = None
    regime: str | None = None
    property_: str | None = Field(None, alias="property")
    limit: int = 20

    model_config = {"populate_by_name": True}


class GraphNode(BaseModel):
    text: str
    type: str
    sources: list[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    verb: str = ""
    sources: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    gaps: list[str] = Field(default_factory=list)


class PDFIngestionResult(BaseModel):
    doc_id: str
    filename: str
    page_count: int
    char_count: int
    language: str
    extraction: ExtractionResult


# ── Graph RAG models ──────────────────────────────────────────────────────────


class RetrievalOutcome(StrEnum):
    """Coarse verdict on what retrieval actually found, so the caller can tell a
    genuine corpus gap apart from a retrieval miss (SPEC gap-handling §A1)."""

    STRONG_CONTEXT = "strong_context"  # exact/lemma anchor + query terms present in prose
    WEAK_CONTEXT = "weak_context"  # nodes found, but prose doesn't cover the query
    NO_ANCHOR = "no_anchor"  # nothing in the graph matched the question at all


class RetrievalContext(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    matched_entities: list[str]
    sources: list[str]
    # Raw text of the most relevant retrieved chunks (Document.text). The graph
    # gives precise entity/relation structure but drops narrative prose
    # (applications, causes, procedures) that never becomes a triple — feeding
    # the actual chunk text alongside the triples lets the LLM answer those too.
    source_texts: list[str] = Field(default_factory=list)
    # Fraction of distinct query terms that actually appear in the fetched
    # source prose (0..1) — the cheap "did we retrieve the right text" signal
    # used to classify the outcome without a second LLM call (SPEC §A1).
    context_relevance: float = 0.0
    outcome: RetrievalOutcome = RetrievalOutcome.STRONG_CONTEXT


class AnswerStatus(StrEnum):
    """How the generator's answer relates to the retrieved context — reported by
    the model itself (structured JSON), replacing the keyword refusal heuristic
    that made the grounded flag flip run-to-run on identical context."""

    GROUNDED = "grounded"  # domain answer supported by the graph context
    UNGROUNDED = "ungrounded"  # domain answer from the model's own knowledge
    NO_DATA = "no_data"  # domain question, no answer available → honest gap
    CASUAL = "casual"  # greeting / thanks / meta small-talk, not a domain question


class GeneratedAnswer(BaseModel):
    answer: str
    status: AnswerStatus = AnswerStatus.GROUNDED


class RAGQuery(BaseModel):
    question: str
    max_hops: int = 2
    max_nodes: int = 20


class RAGResponse(BaseModel):
    answer: str
    context_nodes: list[GraphNode]
    context_edges: list[GraphEdge]
    sources: list[str]
    matched_entities: list[str]
    # Whether the answer is grounded in the retrieved context. False = the model
    # fell back on its own parametric knowledge (or declined for lack of data);
    # surfaced so the UI can lower the confidence badge (SPEC §A5).
    grounded: bool | None = None
    # Machine-readable reason when retrieval could not answer, so the client can
    # phrase an honest degradation ("not in corpus" vs "couldn't match query")
    # instead of a mute refusal (SPEC §A2/§A3). None = normal answer.
    gap_reason: RetrievalOutcome | None = None
