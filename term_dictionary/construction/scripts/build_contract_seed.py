"""Build a high-precision seed from the ontology contract + missing CQ vocabulary.

The ontology's `ontology/contracts.py` (metalcrow @ onthology) already hand-codes
the RU/EN aliases for the target competency questions in two open registries —
`PROCESS_SEED` (process_types) and `QUANTITY_KINDS_SEED` (quantity_kinds). The
critical review found our corpus-only glossary sat *behind* that hand seed
(обессоливание/desalination, сухой остаток/TDS, циркуляция католита, закачка
шахтных вод, current_efficiency, CAPEX/OPEX all missing). This binds our
dictionary to the contract **by reference** (we read the enum/alias names, we do
NOT import the unmerged module) and folds those aliases in as must-keep seed,
plus a small set of CQ terms the contract itself does not spell out
(catholyte/anolyte, cathode/anode, PGM as an EN acronym, mine water).

Emits (all under the repo, consumed by the normal pipeline):
  - data/seed/contract_glossary.jsonl   {term, label, lang} rows (auto-loaded seed)
  - data/contract_must_link.json        [[a, b], ...] RU/EN alias pairs per concept
  - term_dict/data/ontology_registries_snapshot.json  process/quantity registries
        (name -> aliases[]) for the export adapter's dedup + ProcessType resolve.

Label binding (our label -> contract target):
  PROCESS  -> process_types (ProcessType enum member)
  PROPERTY -> quantity_kinds
  MATERIAL/EQUIPMENT -> entity_aliases (material/equipment)

Refresh when contracts.py changes:
  python scripts/build_contract_seed.py --contracts /path/to/ontology/contracts.py
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

import click

logger = logging.getLogger(__name__)

# Repo-relative vendored copy of the ontology contract (see data/reference/).
DEFAULT_CONTRACTS = "data/reference/contracts_onthology.py"

# CQ vocabulary the contract registries do not spell out as standalone terms.
# Each entry is one concept: (label, [RU/EN surface forms grouped as synonyms]).
# Kept deliberately small and high-precision — these close the specific CQ
# headword misses the review flagged.
EXTRA_CQ_CONCEPTS: list[tuple[str, list[str]]] = [
    ("MATERIAL", ["католит", "catholyte"]),
    ("MATERIAL", ["анолит", "anolyte"]),
    ("MATERIAL", ["электролит", "electrolyte"]),
    ("EQUIPMENT", ["катод", "cathode"]),
    ("EQUIPMENT", ["анод", "anode"]),
    ("MATERIAL", ["МПГ", "металлы платиновой группы", "платиновые металлы",
                  "PGM", "platinum group metals"]),
    ("MATERIAL", ["шахтные воды", "рудничные воды", "mine water"]),
    ("MATERIAL", ["штейн", "matte"]),
    ("MATERIAL", ["шлак", "slag"]),
    ("PROPERTY", ["содержание сульфатов", "sulfate content"]),
    ("PROPERTY", ["содержание хлоридов", "chloride content"]),
]


def _lit(node: ast.AST | None):
    """literal_eval that tolerates non-literal nodes (returns None)."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def parse_registries(contracts_path: str) -> tuple[dict, dict]:
    """Extract {process_type -> {enum, aliases[]}} and {quantity -> {unit_dim, aliases[]}}.

    Only reads the seed *literals*; ignores the runtime `resolve()` call that
    also constructs a QuantityKindDef from a variable (which broke a naive walk).
    """
    src = Path(contracts_path).read_text(encoding="utf-8")
    tree = ast.parse(src)

    enum_members: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ProcessType":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    enum_members[stmt.targets[0].id] = stmt.value.value

    process: dict[str, dict] = {}
    quantity: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id == "ProcessDef":
            nm = _kwarg(node, "name")
            enum_attr = nm.attr if isinstance(nm, ast.Attribute) else None
            if enum_attr is None:
                continue
            canon = enum_members.get(enum_attr, enum_attr)
            process[canon] = {
                "enum": enum_attr,
                "aliases": _lit(_kwarg(node, "aliases")) or [],
                "description": _lit(_kwarg(node, "description")) or "",
            }
        elif node.func.id == "QuantityKindDef":
            nm = _lit(_kwarg(node, "name"))
            if not nm:
                continue
            quantity[nm] = {
                "unit_dim": _lit(_kwarg(node, "unit_dim")) or "",
                "aliases": _lit(_kwarg(node, "aliases")) or [],
            }

    # Ensure every ProcessType enum member exists (so overflow resolution can see
    # members that have no ProcessDef seed row).
    for enum_attr, val in enum_members.items():
        process.setdefault(val, {"enum": enum_attr, "aliases": [], "description": ""})
    return process, quantity


def _detect_lang(term: str) -> str:
    return "ru" if any("Ѐ" <= c <= "ӿ" for c in term) else "en"


# ≤2-char pure-Latin aliases collide with chemical-element symbols ("At" is both
# elongation's alias and astatine's symbol; "YS", "pH"). Folding them as must-link
# forces cross-label merges (astatine↔elongation). Drop them from OUR seed and
# report the collision for the onthology author — we never edit the contract.
_AMBIGUOUS_ALIAS_RE = re.compile(r"^[A-Za-z]{1,2}$")


def build(contracts_path: str, repo_root: Path) -> dict:
    process, quantity = parse_registries(contracts_path)

    collisions: list[dict] = []

    def _keep(forms: list[str], canonical: str, label: str) -> list[str]:
        out = []
        for f in forms:
            if f != canonical and _AMBIGUOUS_ALIAS_RE.match(f):
                collisions.append({"alias": f, "member": canonical, "label": label,
                                   "reason": "<=2-char Latin collides with element symbol"})
            else:
                out.append(f)
        return out

    # Concept groups: (label, canonical_name, [surface forms]).
    concepts: list[tuple[str, str, list[str]]] = []
    for name, d in process.items():
        forms = _dedup([name.replace("_", " ")] + list(d["aliases"]))
        concepts.append(("PROCESS", name, _keep(forms, name.replace("_", " "), "PROCESS")))
    for name, d in quantity.items():
        forms = _dedup([name.replace("_", " ")] + list(d["aliases"]))
        concepts.append(("PROPERTY", name, _keep(forms, name.replace("_", " "), "PROPERTY")))
    for label, forms in EXTRA_CQ_CONCEPTS:
        concepts.append((label, forms[0], _dedup(forms)))

    # (a) seed glossary rows.
    seed_rows: list[dict] = []
    # (b) must-link pairs: star each concept's forms to its first form (the
    # canonical anchor) — a star, not a chain, so no transitive drift.
    must_link: list[list[str]] = []
    for label, canon, forms in concepts:
        for f in forms:
            seed_rows.append({"term": f, "label": label, "lang": _detect_lang(f)})
        for f in forms[1:]:
            must_link.append([forms[0], f])

    seed_path = repo_root / "data/seed/contract_glossary.jsonl"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    with seed_path.open("w", encoding="utf-8") as fh:
        for r in seed_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    ml_path = repo_root / "data/contract_must_link.json"
    ml_path.write_text(json.dumps(must_link, ensure_ascii=False, indent=1),
                       encoding="utf-8")

    snap = {
        "_source": "arqoofficial/metalcrow @ onthology (ontology/contracts.py)",
        "_note": "Registry snapshot for term_dict.ontology_export dedup + ProcessType/quantity resolution.",
        "process_types": process,
        "quantity_kinds": quantity,
    }
    snap_path = repo_root / "term_dict/data/ontology_registries_snapshot.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    coll_path = repo_root / "data/contract_alias_collisions.json"
    coll_path.write_text(json.dumps(collisions, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    return {
        "process_types": len(process),
        "quantity_kinds": len(quantity),
        "extra_concepts": len(EXTRA_CQ_CONCEPTS),
        "seed_rows": len(seed_rows),
        "must_link_pairs": len(must_link),
        "dropped_ambiguous_aliases": len(collisions),
        "seed_path": str(seed_path),
        "must_link_path": str(ml_path),
        "snapshot_path": str(snap_path),
        "collisions_path": str(coll_path),
    }


def _dedup(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        x = x.strip()
        k = x.casefold()
        if x and k not in seen:
            seen.add(k)
            out.append(x)
    return out


@click.command()
@click.option("--contracts", "contracts_path", default=DEFAULT_CONTRACTS,
              show_default=False, help="Path to ontology/contracts.py.")
@click.option("--repo-root", default=".", show_default=True)
def main(contracts_path: str, repo_root: str) -> None:
    """Extract contract registries + CQ vocab into seed/must-link/snapshot."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = build(contracts_path, Path(repo_root))
    click.echo(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
