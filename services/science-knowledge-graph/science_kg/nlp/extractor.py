"""Entity and relation extraction from spaCy Doc objects."""

import logging
import re
from spacy.tokens import Doc, Span, Token

from science_kg.models import (
    Entity,
    EntityType,
    Relation,
    RelationType,
    ExtractionResult,
)
from science_kg.nlp.ceder_extractor import extract_ceder_relations
from science_kg.nlp.normalizer import canonical_material, canonical_process

logger = logging.getLogger(__name__)

# ── Verb dictionaries ─────────────────────────────────────────────────────────

_EFFECT_VERBS_RU = {
    "увеличивать",
    "увеличить",
    "повышать",
    "повысить",
    "уменьшать",
    "уменьшить",
    "снижать",
    "снизить",
    "улучшать",
    "улучшить",
    "ухудшать",
    "ухудшить",
    "изменять",
    "изменить",
    "влиять",
    "повлиять",
    "обеспечивать",
    "обеспечить",
    "достигать",
    "достичь",
    "получать",
    "получить",
    "формировать",
    "сформировать",
    "составлять",
    "составить",
    "достигнуть",
    "иметь",
    "показывать",
    "обладать",
    "демонстрировать",
    "сохранять",
    "поддерживать",
    "характеризовать",
}

_EFFECT_VERBS_EN = {
    # existing
    "increase",
    "decrease",
    "improve",
    "reduce",
    "enhance",
    "affect",
    "achieve",
    "reach",
    "obtain",
    "show",
    "demonstrate",
    "exhibit",
    "raise",
    "lower",
    "change",
    "alter",
    "modify",
    "result",
    "yield",
    "provide",
    "produce",
    "enable",
    "allow",
    # commonly missed in materials science papers
    "possess",
    "retain",
    "display",
    "feature",
    "present",
    "maintain",
    "have",
    "reveal",
    "indicate",
    "report",
    "measure",
    "record",
    "observe",
    "find",
    "note",
    "confirm",
    "suggest",
    "correlate",
    "depend",
    "determine",
    "govern",
    "control",
}

_PROCESS_VERBS_RU = {
    "отжигать",
    "отжечь",
    "закалять",
    "закалить",
    "обрабатывать",
    "обработать",
    "нагревать",
    "нагреть",
    "охлаждать",
    "охладить",
    "деформировать",
    "прессовать",
    "испытывать",
    "испытать",
    "исследовать",
}

_PROCESS_VERBS_EN = {
    "anneal",
    "quench",
    "heat",
    "cool",
    "treat",
    "process",
    "age",
    "sinter",
    "forge",
    "roll",
    "deform",
    "fabricate",
    "manufacture",
    "test",
    "examine",
    "investigate",
    "study",
    "measure",
    "subject",
    "prepare",
    "produce",
    "synthesize",
    "deposit",
    "coat",
    "print",
    "solution-treat",
    "normalize",
    "temper",
    "stress-relieve",
}

_EFFECT_VERBS = _EFFECT_VERBS_RU | _EFFECT_VERBS_EN
_PROCESS_VERBS = _PROCESS_VERBS_RU | _PROCESS_VERBS_EN

# ── Relation type mapping ─────────────────────────────────────────────────────

_VALID_ENTITY_TYPES = {t.value for t in EntityType}

# bc5cdr NER labels → our EntityType (DISEASE is not mapped — discarded).
# PER/ORG come from ru_core_news_sm's general-purpose NER (en_core_sci_sm
# doesn't emit these labels — Expert/Facility for English docs come only from
# the FACILITY_PATTERNS dictionary in nlp/patterns.py).
_LABEL_REMAP: dict[str, str] = {
    "CHEMICAL": EntityType.MATERIAL.value,
    "PER": EntityType.EXPERT.value,
    "ORG": EntityType.FACILITY.value,
}

# Symmetric by design: dep-tree voice (active/passive) can put either member
# of a pair in subject or object position (e.g. "SEM measured hardness" vs
# "hardness was measured by SEM") — both orderings must map to the same
# relation type, same as the pre-rename map did for (REGIME, MATERIAL).
_RELATION_MAP: dict[tuple[EntityType, EntityType], RelationType] = {
    (EntityType.PROCESS, EntityType.MATERIAL): RelationType.USES_MATERIAL,
    (EntityType.MATERIAL, EntityType.PROCESS): RelationType.USES_MATERIAL,
    (EntityType.EXPERIMENT, EntityType.MATERIAL): RelationType.USES_MATERIAL,
    (EntityType.MATERIAL, EntityType.EXPERIMENT): RelationType.USES_MATERIAL,
    (EntityType.PROCESS, EntityType.PROCESS): RelationType.OPERATES_AT_CONDITION,
    (EntityType.MATERIAL, EntityType.PROPERTY): RelationType.PRODUCES_OUTPUT,
    (EntityType.PROCESS, EntityType.PROPERTY): RelationType.PRODUCES_OUTPUT,
    (EntityType.EXPERIMENT, EntityType.PROPERTY): RelationType.PRODUCES_OUTPUT,
    (EntityType.EQUIPMENT, EntityType.MATERIAL): RelationType.VALIDATED_BY,
    (EntityType.MATERIAL, EntityType.EQUIPMENT): RelationType.VALIDATED_BY,
    (EntityType.EQUIPMENT, EntityType.PROPERTY): RelationType.VALIDATED_BY,
    (EntityType.PROPERTY, EntityType.EQUIPMENT): RelationType.VALIDATED_BY,
    (EntityType.EQUIPMENT, EntityType.EXPERIMENT): RelationType.VALIDATED_BY,
    (EntityType.EXPERIMENT, EntityType.EQUIPMENT): RelationType.VALIDATED_BY,
}

# ── Helpers ───────────────────────────────────────────────────────────────────


def _ent_type(token: Token) -> EntityType | None:
    if token.ent_type_ in _VALID_ENTITY_TYPES:
        return EntityType(token.ent_type_)
    return None


def _full_span_text(token: Token) -> str:
    """Return the full entity span text for any token that belongs to a span."""
    for ent in token.doc.ents:
        if ent.start <= token.i < ent.end:
            return ent.text
    return token.text


def _add_prep_entities(prep_token: Token, bucket: list[Token]) -> None:
    """Walk prep → pobj (PTB) or nmod children (UD) to collect entity tokens."""
    for gc in prep_token.children:
        if _ent_type(gc):
            bucket.append(gc)
        for ggc in gc.children:
            if _ent_type(ggc):
                bucket.append(ggc)


def _collect_subjects_objects(token: Token) -> tuple[list[Token], list[Token]]:
    """
    Return (subjects, objects) for a verb in both active and passive voice.

    Handles two dep-tree conventions:
    - Universal Dependencies (en_core_sci_sm, RU model): obl, nsubj, obj, nmod
    - Penn Treebank (en_core_web_sm): prep+pobj, nsubj, dobj, nsubjpass, agent

    Active voice:  "820°C increased hardness"
        nsubj → subject (semantic cause)
        obj / dobj / obl / nmod → object (semantic target)

    Passive voice: "hardness was increased by aging at 820°C"
        nsubjpass → semantic target (becomes object)
        agent / obl / prep / nmod → semantic source (becomes subject)

    Passive + no agent: "tensile strength was achieved"
        nsubjpass → object; no subject found → relation skipped
    """
    is_passive = any(c.dep_ == "nsubjpass" for c in token.children)

    subjects: list[Token] = []
    objects: list[Token] = []

    for child in token.children:
        child_ent = _ent_type(child)

        if is_passive:
            if child.dep_ == "nsubjpass":
                if child_ent:
                    objects.append(child)
                else:
                    # Entity embedded as compound/nmod of nsubjpass head
                    # e.g. "Ti-6Al-4V specimens were treated" → compound Ti-6Al-4V
                    for gc in child.children:
                        if gc.dep_ in ("compound", "nmod") and _ent_type(gc):
                            objects.append(gc)

            elif child.dep_ == "agent":
                # PTB: "by X" → agent → pobj
                for gc in child.children:
                    if _ent_type(gc):
                        subjects.append(gc)
                    for ggc in gc.children:
                        if _ent_type(ggc):
                            subjects.append(ggc)
                if child_ent:
                    subjects.append(child)

            elif child.dep_ in ("obl", "prep", "nmod"):
                # Prepositional/nominal modifier → semantic source/instrument
                if child_ent:
                    subjects.append(child)
                _add_prep_entities(child, subjects)

        else:
            # Active voice
            if child.dep_ == "nsubj":
                if child_ent:
                    subjects.append(child)
                else:
                    # Entity embedded as compound/nmod of nsubj head
                    # e.g. "Ti-6Al-4V specimens show strength" → compound Ti-6Al-4V
                    # e.g. "treatment of Ti-6Al-4V can result in..." → nmod Ti-6Al-4V
                    for gc in child.children:
                        if gc.dep_ in ("compound", "nmod") and _ent_type(gc):
                            subjects.append(gc)
                        # one level deeper for "treatment of alloy Ti-6Al-4V"
                        for ggc in gc.children:
                            if ggc.dep_ in ("compound", "nmod") and _ent_type(ggc):
                                subjects.append(ggc)

            elif child.dep_ in ("obj", "dobj", "iobj"):
                if child_ent:
                    objects.append(child)
                else:
                    # Entity embedded in object NP
                    # e.g. "produced Ti-6Al-4V specimens" → compound Ti-6Al-4V
                    for gc in child.children:
                        if gc.dep_ in ("compound", "nmod") and _ent_type(gc):
                            objects.append(gc)

            elif child.dep_ in ("obl", "prep", "nmod"):
                if child_ent:
                    objects.append(child)
                _add_prep_entities(child, objects)

    return subjects, objects


# ── Verbless pattern: "PROPERTY of MATERIAL was PROPERTY(value)" ─────────────
#
# Handles sentences where the relation is implicit — no action verb, just a
# nominal "X of Y" or "X was Z" structure:
#   "The tensile strength of Ti-6Al-4V was 980 MPa."
#   "Hardness of the alloy reached 42 HRC."
#
# Strategy: for each PROPERTY span, walk its dep-subtree looking for a
# MATERIAL or PROCESS anchor via "of" (nmod/prep) or copula/attr links.


def _extract_verbless_relations(
    doc: Doc, source_doc: str, seen: set[tuple[str, str, str]]
) -> list[Relation]:
    relations: list[Relation] = []

    for sent in doc.sents:
        # Index entity spans by their root token index for fast lookup
        ent_by_root: dict[int, Span] = {}
        for ent in sent.ents:
            label = _LABEL_REMAP.get(ent.label_, ent.label_)
            if label in _VALID_ENTITY_TYPES:
                ent_by_root[ent.root.i] = ent

        for token in sent:
            ent_span = ent_by_root.get(token.i)
            if ent_span is None:
                continue

            src_label = _LABEL_REMAP.get(ent_span.label_, ent_span.label_)
            if src_label not in (
                EntityType.PROPERTY,
                EntityType.MATERIAL,
                EntityType.PROCESS,
            ):
                continue

            # Walk nmod/prep children looking for a paired entity
            for child in token.children:
                if child.dep_ not in ("nmod", "prep", "of"):
                    continue
                # direct child is entity
                tgt_span = ent_by_root.get(child.i)
                if tgt_span:
                    _try_add(ent_span, tgt_span, "nmod-of", source_doc, seen, relations)
                # grandchild (pobj in PTB style)
                for gc in child.children:
                    tgt_span = ent_by_root.get(gc.i)
                    if tgt_span:
                        _try_add(
                            ent_span, tgt_span, "nmod-of", source_doc, seen, relations
                        )

            # copula link: "strength was 980 MPa" → attr child
            for child in token.children:
                if child.dep_ in ("attr", "appos"):
                    tgt_span = ent_by_root.get(child.i)
                    if tgt_span:
                        _try_add(
                            ent_span, tgt_span, "copula", source_doc, seen, relations
                        )

    return relations


def _try_add(
    src_span: Span,
    tgt_span: Span,
    verb: str,
    source_doc: str,
    seen: set,
    relations: list,
) -> None:
    src_label = _LABEL_REMAP.get(src_span.label_, src_span.label_)
    tgt_label = _LABEL_REMAP.get(tgt_span.label_, tgt_span.label_)
    if src_label not in _VALID_ENTITY_TYPES or tgt_label not in _VALID_ENTITY_TYPES:
        return
    src_type = EntityType(src_label)
    tgt_type = EntityType(tgt_label)
    rel_type = _RELATION_MAP.get((src_type, tgt_type))
    if rel_type is None:
        return
    src_text = _normalize(src_span.text, src_label)
    tgt_text = _normalize(tgt_span.text, tgt_label)
    if not _is_valid(src_text, src_label) or not _is_valid(tgt_text, tgt_label):
        return
    key = (src_text, rel_type.value, tgt_text)
    if key in seen:
        return
    seen.add(key)
    relations.append(
        Relation(
            source=src_text,
            source_type=src_type,
            relation=rel_type,
            target=tgt_text,
            target_type=tgt_type,
            verb=verb,
            source_doc=source_doc,
        )
    )


# ── Normalization & filtering ─────────────────────────────────────────────────

# Compiled once — reused across all calls
_RE_DEGREE_SPACE = re.compile(r"\s*°\s*([CFKcfk])\b")  # "600 ° C" → "600°C"
_RE_OCR_DEGREE = re.compile(r"\b([oc])\b", re.I)  # used only after number
_RE_OCR_OC = re.compile(r"(\d[\d.]*)\s*[oO][Cc]\b")  # "1020 oC" → "1020°C"
_RE_OCR_OF = re.compile(r"(\d[\d.]*)\s*[oO][Ff]\b")  # "800 oF" → "800°F"
_RE_MULTI_SPACE = re.compile(r" {2,}")


def _normalize(text: str, label: str) -> str:
    """Canonical text form for an entity, used for dedup and KG node identity."""
    t = text.strip()

    if label == EntityType.PROCESS:
        return canonical_process(t)

    if label == EntityType.MATERIAL:
        return canonical_material(t)

    if label == EntityType.PROPERTY:
        words = t.split()
        return " ".join(
            w if (w.isupper() and len(w) <= 4) else w.lower() for w in words
        )

    # EQUIPMENT / EXPERIMENT / PUBLICATION / EXPERT / FACILITY: just strip
    return t


_SINGLE_LETTER = re.compile(r"^[A-Za-z]$")
_MIN_LEN: dict[str, int] = {
    EntityType.PROCESS: 3,  # "2h" is ok, bare "C" is not
    EntityType.MATERIAL: 2,  # "Ti" is ok
    EntityType.PROPERTY: 2,  # also covers old VALUE ("42 HRC")
    EntityType.EQUIPMENT: 2,
    EntityType.EXPERIMENT: 2,
    EntityType.PUBLICATION: 1,
    EntityType.EXPERT: 2,
    EntityType.FACILITY: 2,
}


def _is_valid(text: str, label: str) -> bool:
    """Return False for obvious noise: bare letters, too short, pure punctuation."""
    t = text.strip()
    if not t:
        return False
    min_len = _MIN_LEN.get(label, 2)
    if len(t) < min_len:
        return False
    # single letter that isn't a valid chemical symbol context → noise
    if _SINGLE_LETTER.match(t):
        return False
    # all-digit strings as PROPERTY are noise
    if label == EntityType.PROPERTY and t.isdigit():
        return False
    return True


# ── Public API ────────────────────────────────────────────────────────────────


def extract_entities(doc: Doc, source_doc: str = "") -> list[Entity]:
    entities: list[Entity] = []
    seen_spans: set[tuple[int, int]] = set()
    # secondary dedup: same canonical text+label after normalization
    seen_canonical: set[tuple[str, str]] = set()

    for ent in doc.ents:
        label = _LABEL_REMAP.get(ent.label_, ent.label_)
        if label not in _VALID_ENTITY_TYPES:
            continue

        key = (ent.start_char, ent.end_char)
        if key in seen_spans:
            continue
        seen_spans.add(key)

        normalized = _normalize(ent.text, label)
        if not _is_valid(normalized, label):
            continue

        canon_key = (normalized, label)
        if canon_key in seen_canonical:
            continue
        seen_canonical.add(canon_key)

        entities.append(
            Entity(
                text=normalized,
                label=EntityType(label),
                start_char=ent.start_char,
                end_char=ent.end_char,
                source_doc=source_doc,
            )
        )
    return entities


def extract_relations(doc: Doc, source_doc: str = "") -> list[Relation]:
    """
    Extract typed relations via:
    1. Verb-anchored dep-tree (active + passive voice)
    2. Verbless nominal patterns ("strength of Ti-6Al-4V was 980 MPa")
    """
    relations: list[Relation] = []
    seen: set[tuple[str, str, str]] = set()

    # ── 1. Verb-anchored ─────────────────────────────────────────────────────
    for token in doc:
        if token.pos_ != "VERB":
            continue

        lemma = token.lemma_.lower()
        if lemma not in _EFFECT_VERBS and lemma not in _PROCESS_VERBS:
            continue

        subjects, objects = _collect_subjects_objects(token)

        if not subjects or not objects:
            continue

        for subj in subjects:
            for obj in objects:
                if subj == obj:
                    continue

                src_type = _ent_type(subj)
                tgt_type = _ent_type(obj)
                rel_type = (
                    _RELATION_MAP.get((src_type, tgt_type))
                    if src_type and tgt_type
                    else None
                )

                if rel_type is None:
                    if src_type and tgt_type:
                        logger.debug(
                            "unmapped pair (%s, %s) doc=%s verb=%s",
                            src_type,
                            tgt_type,
                            source_doc,
                            lemma,
                        )
                    continue

                src_text = _normalize(_full_span_text(subj), src_type.value)
                tgt_text = _normalize(_full_span_text(obj), tgt_type.value)
                if not _is_valid(src_text, src_type.value) or not _is_valid(
                    tgt_text, tgt_type.value
                ):
                    continue

                key = (src_text, rel_type.value, tgt_text)
                if key in seen:
                    continue
                seen.add(key)

                relations.append(
                    Relation(
                        source=src_text,
                        source_type=src_type,
                        relation=rel_type,
                        target=tgt_text,
                        target_type=tgt_type,
                        verb=lemma,
                        source_doc=source_doc,
                    )
                )

    # ── 2. Verbless nominal patterns ─────────────────────────────────────────
    relations.extend(_extract_verbless_relations(doc, source_doc, seen))

    return relations


def _add_publication_edges(
    entities: list[Entity], relations: list[Relation], doc_id: str
) -> None:
    """`described_in` isn't a grammar-based relation (unlike the rest of
    _RELATION_MAP) — every entity found in a document is, by construction,
    described in that document. One Publication node per doc_id (text =
    doc_id — there's no separate title/meta threaded through process_document),
    edges from every other distinct entity in the doc to it. Mutates
    `relations` in place; `entities` is read-only here."""
    seen = {(r.source, r.relation.value, r.target) for r in relations}
    for ent in entities:
        if ent.label == EntityType.PUBLICATION:
            continue
        key = (ent.text, RelationType.DESCRIBED_IN.value, doc_id)
        if key in seen:
            continue
        seen.add(key)
        relations.append(
            Relation(
                source=ent.text,
                source_type=ent.label,
                relation=RelationType.DESCRIBED_IN,
                target=doc_id,
                target_type=EntityType.PUBLICATION,
                verb="",
                source_doc=doc_id,
            )
        )


def process_document(doc: Doc, doc_id: str) -> ExtractionResult:
    """Dep-tree relations + Ceder rule-based temperature/time/atmosphere
    conditions (§ceder_extractor), deduplicated by (source, relation, target) —
    same merge strategy previously only exercised by the standalone
    run_pdf_pipeline.py script, now applied to every ingestion path. Also adds
    one described_in edge per entity to a Publication node for this doc_id
    (see _add_publication_edges) — the only relation not produced by grammar."""
    relations = extract_relations(doc, source_doc=doc_id)
    seen = {(r.source, r.relation.value, r.target) for r in relations}

    for rel in extract_ceder_relations(doc.text, doc_id=doc_id):
        key = (rel.source, rel.relation.value, rel.target)
        if key not in seen:
            seen.add(key)
            relations.append(rel)

    entities = extract_entities(doc, source_doc=doc_id)
    _add_publication_edges(entities, relations, doc_id)

    return ExtractionResult(
        doc_id=doc_id,
        entities=entities,
        relations=relations,
    )
