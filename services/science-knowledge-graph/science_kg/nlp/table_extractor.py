"""Extract structured (material, property, value) relations from PDF tables."""

import re
from science_kg.models import Relation, RelationType, EntityType
from science_kg.nlp.normalizer import canonical_material, canonical_regime

# ── Header → canonical property name ─────────────────────────────────────────
_PROP_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"yield.strength|yield.str", re.I), "yield strength"),
    (re.compile(r"ultimate.tensile|UTS|tensile.str", re.I), "tensile strength"),
    (re.compile(r"elongat", re.I), "elongation"),
    (re.compile(r"hardness|HV\b|HRC|HRB", re.I), "hardness"),
    (re.compile(r"fracture.tough|K[iI][cC]", re.I), "fracture toughness"),
    (re.compile(r"creep\s*strain", re.I), "creep strain"),
    (re.compile(r"\bcreep\b", re.I), "creep"),
    (re.compile(r"time.to.rupture|rupture", re.I), "time to rupture"),
    (re.compile(r"modulus|Young", re.I), "modulus"),
    (re.compile(r"microstructure", re.I), "microstructure"),
    (re.compile(r"temperature|temp\b", re.I), "temperature"),
    (re.compile(r"holding.time|time\b", re.I), "holding time"),
    (re.compile(r"pressure", re.I), "pressure"),
    (re.compile(r"atmosphere|atmos", re.I), "atmosphere"),
]

_NUM_RE = re.compile(r"^\s*[~≈]?[-–]?\d[\d.,\s±×eE+\-]*\s*$")
_UNIT_RE = re.compile(r"\(([^)]+)\)")
_TEMP_RE = re.compile(r"^\s*[~≈]?\d[\d.]*\s*°?[CF]?\s*$")


def _is_numeric(cell: str) -> bool:
    return bool(_NUM_RE.match(cell.strip()))


def _extract_unit(header: str) -> str:
    m = _UNIT_RE.search(header)
    return m.group(1).strip() if m else ""


def _clean_num(cell: str) -> str:
    """Remove ± uncertainty, keep value: '557 ± 7' → '557'."""
    return re.sub(r"\s*[±]\s*[\d.]+", "", cell).strip()


def _to_property(header: str) -> str | None:
    h = header.replace("\n", " ").strip()
    for pattern, name in _PROP_PATTERNS:
        if pattern.search(h):
            return name
    return None


def _is_regime_header(header: str) -> bool:
    h = header.lower().replace("\n", " ")
    return any(
        kw in h
        for kw in (
            "temperature",
            "temp",
            "time",
            "pressure",
            "atmosphere",
            "holding",
            "duration",
        )
    )


# ── Main extraction function ──────────────────────────────────────────────────


def extract_table_relations(
    table: list[list[str | None]], doc_id: str = ""
) -> list[Relation]:
    """
    Extract typed Relation objects from a structured PDF table.

    Handles two orientations:
      Normal    — rows = specimens/materials, columns = properties
      Transposed — rows = properties/conditions, columns = treatments/processes

    Returns empty list if table structure is not recognised.
    """
    if not table or len(table) < 2:
        return []

    rows = [[str(c or "").replace("\n", " ").strip() for c in row] for row in table]
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]

    # ── Try Normal orientation (row 0 = headers, col 0 = specimen IDs) ───────
    relations = _try_normal(rows, doc_id)
    if relations:
        return relations

    # ── Try Transposed orientation (col 0 = property names, row 0 = process) ─
    return _try_transposed(rows, doc_id)


def _try_normal(rows: list[list[str]], doc_id: str) -> list[Relation]:
    """Rows = specimens, columns = properties."""
    header = rows[0]
    prop_cols: list[tuple[int, str, str]] = []  # (col_idx, prop_name, unit)

    for j, h in enumerate(header[1:], 1):
        prop = _to_property(h)
        if prop:
            unit = _extract_unit(h)
            prop_cols.append((j, prop, unit))

    if not prop_cols:
        return []

    relations: list[Relation] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows[1:]:
        specimen = row[0] if row else ""
        if not specimen or _is_numeric(specimen) or len(specimen) < 2:
            continue
        material = canonical_material(specimen)

        for j, prop, unit in prop_cols:
            if j >= len(row):
                continue
            raw = row[j]
            if not raw or not _is_numeric(raw):
                continue
            val_text = _clean_num(raw)
            if unit:
                val_text = f"{val_text} {unit}"

            # MATERIAL --AFFECTS--> PROPERTY
            key_mp = (material, RelationType.AFFECTS, prop)
            if key_mp not in seen:
                seen.add(key_mp)
                relations.append(
                    Relation(
                        source=material,
                        source_type=EntityType.MATERIAL,
                        relation=RelationType.AFFECTS,
                        target=prop,
                        target_type=EntityType.PROPERTY,
                        verb="table",
                        source_doc=doc_id,
                    )
                )

            # MATERIAL --AFFECTS--> VALUE (numeric+unit)
            key_mv = (material, RelationType.AFFECTS, val_text)
            if key_mv not in seen:
                seen.add(key_mv)
                relations.append(
                    Relation(
                        source=material,
                        source_type=EntityType.MATERIAL,
                        relation=RelationType.AFFECTS,
                        target=val_text,
                        target_type=EntityType.VALUE,
                        verb="table",
                        source_doc=doc_id,
                    )
                )

    return relations


def _try_transposed(rows: list[list[str]], doc_id: str) -> list[Relation]:
    """Rows = property/condition names, columns = processes/treatments."""
    # Col 0 = property labels; row 0 = process/treatment names
    process_cols: list[tuple[int, str]] = []
    for j, cell in enumerate(rows[0][1:], 1):
        if cell:
            process_cols.append((j, cell))

    if not process_cols:
        return []

    relations: list[Relation] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows[1:]:
        row_label = row[0] if row else ""
        if not row_label:
            continue

        prop = _to_property(row_label)
        unit = _extract_unit(row_label)
        is_regime = _is_regime_header(row_label)

        for j, process in process_cols:
            if j >= len(row):
                continue
            cell = row[j].strip()
            if not cell:
                continue

            if _is_numeric(cell):
                val_text = _clean_num(cell)
                if unit:
                    val_text = f"{val_text} {unit}"

                if is_regime:
                    # (process) --PROCESSED_BY-- (regime value)
                    regime = canonical_regime(val_text)
                    key = (process, RelationType.PROCESSED_BY, regime)
                    if key not in seen:
                        seen.add(key)
                        relations.append(
                            Relation(
                                source=regime,
                                source_type=EntityType.REGIME,
                                relation=RelationType.PROCESSED_BY,
                                target=canonical_material(process),
                                target_type=EntityType.MATERIAL,
                                verb="table",
                                source_doc=doc_id,
                            )
                        )
                elif prop:
                    # (process/material) --AFFECTS-- (property value)
                    mat = canonical_material(process)
                    key_mp = (mat, RelationType.AFFECTS, prop)
                    if key_mp not in seen:
                        seen.add(key_mp)
                        relations.append(
                            Relation(
                                source=mat,
                                source_type=EntityType.MATERIAL,
                                relation=RelationType.AFFECTS,
                                target=prop,
                                target_type=EntityType.PROPERTY,
                                verb="table",
                                source_doc=doc_id,
                            )
                        )
                    key_mv = (mat, RelationType.AFFECTS, val_text)
                    if key_mv not in seen:
                        seen.add(key_mv)
                        relations.append(
                            Relation(
                                source=mat,
                                source_type=EntityType.MATERIAL,
                                relation=RelationType.AFFECTS,
                                target=val_text,
                                target_type=EntityType.VALUE,
                                verb="table",
                                source_doc=doc_id,
                            )
                        )
            else:
                # Non-numeric cell (e.g. atmosphere = "Argon")
                if is_regime or _to_property(row_label) == "atmosphere":
                    regime = canonical_regime(cell)
                    mat = canonical_material(process)
                    key = (regime, RelationType.PROCESSED_BY, mat)
                    if key not in seen:
                        seen.add(key)
                        relations.append(
                            Relation(
                                source=regime,
                                source_type=EntityType.REGIME,
                                relation=RelationType.PROCESSED_BY,
                                target=mat,
                                target_type=EntityType.MATERIAL,
                                verb="table",
                                source_doc=doc_id,
                            )
                        )

    return relations
