"""Wikidata interwiki harvest → free high-precision RU↔EN term pairs.

Corpus-independent seed source. Wikidata stores one concept (QID) with labels
and aliases in every language, so the EN label/aliases and the RU label/aliases
of the *same* QID are ground-truth cross-lingual synonyms. We harvest them and
hand the clusterer explicit **must-link** edges — no need to make LaBSE
rediscover a pairing Wikidata already asserts.

Two harvest modes:

1. **Curated anchors** — a committed, human-verified TSV of
   ``qid, en, ru, label`` rows (see ``data/wikidata_anchors.tsv``). Each anchor
   is one domain concept whose type we already know, so its label is trusted.
2. **Element sweep** — every ``instance of`` (P31) ``chemical element``
   (Q11344). ~118 clean rows, all MATERIAL, all with RU+EN labels.

Every raw HTTP response is cached under ``data/wikidata_cache/`` so reruns are
offline and reproducible (the endpoint is queried at most once per key). This
module only touches *public* Wikidata — never the untrusted corpus — so it is
safe to run without the parsing gate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

WD_SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "nornickel-kg-termdict/0.1 (research; A2A team)"

ELEMENT_QID = "Q11344"  # chemical element — P31 sweep → the periodic table
DEFAULT_CACHE = Path("data/wikidata_cache")
DEFAULT_ANCHORS = Path("data/wikidata_anchors.tsv")


@dataclass
class WdConcept:
    """One Wikidata concept with all its RU/EN surface forms."""

    qid: str
    label: str  # our entity label (MATERIAL/PROCESS/…), UNKNOWN if unmapped
    canonical_en: str
    canonical_ru: str
    en: list[str] = field(default_factory=list)  # en label + en aliases
    ru: list[str] = field(default_factory=list)  # ru label + ru aliases

    def surface_forms(self) -> list[tuple[str, str]]:
        """All (term, lang) surface forms, de-duplicated, order-stable."""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for term in self.en:
            k = term.casefold()
            if term and k not in seen:
                seen.add(k)
                out.append((term, "en"))
        for term in self.ru:
            k = term.casefold()
            if term and k not in seen:
                seen.add(k)
                out.append((term, "ru"))
        return out

    def must_link_pairs(self) -> list[tuple[str, str]]:
        """Star-shaped must-link edges: canonical ↔ every other surface form."""
        forms = [t for t, _ in self.surface_forms()]
        if not forms:
            return []
        head = self.canonical_en or forms[0]
        return [(head, t) for t in forms if t != head]


class WikidataHarvester:
    """Polite, cached Wikidata client for the interwiki term harvest."""

    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE,
                 min_interval: float = 0.34) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval  # ~3 req/s, well under WD limits
        self._last = 0.0

    # --- low-level HTTP with on-disk cache + backoff -------------------------

    def _cache_path(self, key: str) -> Path:
        safe = urllib.parse.quote(key, safe="")[:180]
        return self.cache_dir / f"{safe}.json"

    def _get_json(self, url: str, cache_key: str, monotonic: float) -> dict:
        """GET ``url`` as JSON, cached by ``cache_key``.

        ``monotonic`` is a caller-supplied clock reading (``time.monotonic``);
        passed in so this module never calls a wall-clock fn directly.
        """
        cp = self._cache_path(cache_key)
        if cp.exists():
            return json.loads(cp.read_text(encoding="utf-8"))
        # simple client-side rate limit
        wait = self.min_interval - (monotonic - self._last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.load(r)
                self._last = time.monotonic()
                cp.write_text(json.dumps(data, ensure_ascii=False),
                              encoding="utf-8")
                return data
            except urllib.error.HTTPError as e:
                last_err = e
                # Honor Retry-After on rate-limit (WD sends seconds; default 60).
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if e.code == 429:
                    delay = int(retry_after) if (retry_after or "").isdigit() else 60
                    logger.warning("WD 429 rate-limited; sleeping %ds (attempt %d)",
                                   delay, attempt + 1)
                    time.sleep(delay)
                else:
                    logger.warning("WD HTTP %d retry %d", e.code, attempt + 1)
                    time.sleep(2 * (attempt + 1))
            except Exception as e:  # network blip / timeout
                last_err = e
                logger.warning("WD request retry %d (%s)", attempt + 1,
                               type(e).__name__)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Wikidata request failed: {cache_key}") from last_err

    def get_json(self, url: str, cache_key: str) -> dict:
        """Public cached GET → parsed JSON.

        Thin wrapper over the tested rate-limit/backoff/cache path so sibling
        harvesters (e.g. the Wikipedia langlinks harvest) can reuse one polite
        HTTP client and share the on-disk cache under ``data/wikidata_cache/``.
        """
        return self._get_json(url, cache_key, time.monotonic())

    def sparql(self, query: str, cache_key: str) -> list[dict]:
        url = WD_SPARQL + "?format=json&query=" + urllib.parse.quote(query)
        # Content-address the cache on the query text so an edited query never
        # returns a stale cached result under the same human-readable label.
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]
        data = self._get_json(url, f"sparql_{cache_key}_{digest}",
                              time.monotonic())
        return data["results"]["bindings"]

    def resolve_by_label(self, terms: list[str]) -> dict[str, list[dict]]:
        """Resolve EN terms to QID candidates via SPARQL exact-label match.

        One batched SPARQL query (no throttled API). For each term returns
        candidates that carry a Russian label (so every hit is a genuine
        cross-lingual pair), each with its EN description and sitelink count.
        Sitelinks alone mislead on homonyms whose non-domain sense is more
        notable (``flotation`` → IPO, ``roasting`` → cooking), so ranking is
        left to :func:`resolve_concepts`, which weighs domain relevance first.
        Returns ``{term_lower: [{qid, ru, desc, sites}, …]}`` (sitelink order).
        """
        result: dict[str, list[dict]] = {t.casefold(): [] for t in terms}
        for i in range(0, len(terms), 40):
            batch = terms[i:i + 40]
            vals = " ".join(f'"{_esc(t)}"@en' for t in batch)
            q = (f"SELECT ?term ?item ?ru ?desc (COUNT(?sl) AS ?sites) WHERE {{"
                 f" VALUES ?term {{ {vals} }}"
                 f" {{ ?item rdfs:label ?term . }}"
                 f" UNION {{ ?item skos:altLabel ?term . }}"
                 f' ?item rdfs:label ?ru . FILTER(LANG(?ru)="ru")'
                 f' OPTIONAL {{ ?item schema:description ?desc .'
                 f' FILTER(LANG(?desc)="en") }}'
                 f" OPTIONAL {{ ?sl schema:about ?item . }} }}"
                 f" GROUP BY ?term ?item ?ru ?desc ORDER BY DESC(?sites)")
            rows = self.sparql(q, f"resolve_{i}_{len(batch)}")
            for b in rows:
                term = b["term"]["value"].casefold()
                result.setdefault(term, []).append({
                    "qid": b["item"]["value"].rsplit("/", 1)[-1],
                    "ru": b["ru"]["value"],
                    "desc": b.get("desc", {}).get("value", ""),
                    "sites": int(b["sites"]["value"]),
                })
        for term in result:
            result[term].sort(key=lambda c: -c["sites"])
        return result

    # --- entity labels + aliases (SPARQL, no throttled API) -----------------

    def fetch_labels_aliases(self, qids: list[str]) -> dict[str, dict]:
        """Return ``{qid: {"en":[...], "ru":[...]}}`` labels+aliases via SPARQL.

        Batches QIDs into ``VALUES`` blocks; collects rdfs:label (canonical,
        first) plus skos:altLabel (aliases) for en and ru.
        """
        out: dict[str, dict] = {}
        for i in range(0, len(qids), 200):
            batch = qids[i:i + 200]
            vals = " ".join(f"wd:{q}" for q in batch)
            q = (f"SELECT ?item ?en ?ru ?enAlt ?ruAlt WHERE {{"
                 f" VALUES ?item {{ {vals} }}"
                 f' OPTIONAL {{ ?item rdfs:label ?en . FILTER(LANG(?en)="en") }}'
                 f' OPTIONAL {{ ?item rdfs:label ?ru . FILTER(LANG(?ru)="ru") }}'
                 f' OPTIONAL {{ ?item skos:altLabel ?enAlt . FILTER(LANG(?enAlt)="en") }}'
                 f' OPTIONAL {{ ?item skos:altLabel ?ruAlt . FILTER(LANG(?ruAlt)="ru") }} }}')
            rows = self.sparql(q, f"labels_{i}_{len(batch)}")
            # Accumulate: labels are constant per item; aliases fan out per row.
            acc: dict[str, dict] = {}
            for b in rows:
                qid = b["item"]["value"].rsplit("/", 1)[-1]
                d = acc.setdefault(qid, {"en_label": "", "ru_label": "",
                                         "en_alt": [], "ru_alt": []})
                if "en" in b:
                    d["en_label"] = b["en"]["value"]
                if "ru" in b:
                    d["ru_label"] = b["ru"]["value"]
                if "enAlt" in b:
                    d["en_alt"].append(b["enAlt"]["value"])
                if "ruAlt" in b:
                    d["ru_alt"].append(b["ruAlt"]["value"])
            for qid, d in acc.items():
                out[qid] = {
                    "en": _dedup([d["en_label"], *d["en_alt"]]),
                    "ru": _dedup([d["ru_label"], *d["ru_alt"]]),
                }
        return out

    # --- harvest entry points -----------------------------------------------

    def harvest_elements(self) -> list[str]:
        """QIDs of every chemical element (P31 = Q11344)."""
        q = (f"SELECT DISTINCT ?item WHERE {{ ?item wdt:P31 wd:{ELEMENT_QID} ."
             f' ?item rdfs:label ?ru . FILTER(LANG(?ru)="ru")'
             f' ?item rdfs:label ?en . FILTER(LANG(?en)="en") }}')
        rows = self.sparql(q, "elements_p31")
        qids = [r["item"]["value"].rsplit("/", 1)[-1] for r in rows]
        logger.info("Element sweep: %d elements", len(qids))
        return qids

    def build_concepts(self, anchors_path: str | Path = DEFAULT_ANCHORS,
                       include_elements: bool = True) -> list[WdConcept]:
        """Load anchors + element sweep → fully-populated WdConcept list."""
        anchors = load_anchor_file(anchors_path)
        label_of: dict[str, str] = {qid: lab for qid, lab in anchors}
        if include_elements:
            for qid in self.harvest_elements():
                label_of.setdefault(qid, "MATERIAL")

        qids = list(label_of)
        la = self.fetch_labels_aliases(qids)
        concepts: list[WdConcept] = []
        for qid in qids:
            info = la.get(qid)
            if not info:
                continue
            # Keep the canonical label always; filter aliases to term-like forms
            # (drops "element 28", "₂₈Ni", brand codes like "Paragard T 380A").
            en = _clean_forms(info["en"])
            ru = _clean_forms(info["ru"])
            if not en or not ru:  # need a genuine cross-lingual pair
                continue
            concepts.append(WdConcept(
                qid=qid, label=label_of[qid],
                canonical_en=en[0], canonical_ru=ru[0], en=en, ru=ru))
        logger.info("Built %d Wikidata concepts (%d anchors + elements)",
                    len(concepts), len(anchors))
        return concepts


# Description keywords marking a candidate as genuinely mining/metallurgy —
# used to pick the DOMAIN sense over a more-notable homonym (flotation=IPO,
# roasting=cooking, crusher=police slang, matte=picture framing).
_DOMAIN_KW = (
    "metal", "ore", "mining", "metallurg", "smelt", "chemical element",
    "mineral", "furnace", "alloy", "leach", "flotation", "froth", "ferro",
    "acid", "oxide", "sulfid", "sulfide", "sulphide", "electrol", "electro",
    "slag", "matte", "crush", "grind", "comminut", "mill", "extraction",
    "refin", "roast", "smelt", "sinter", "calcin", "filtrat", "cathode",
    "anode", "concentrat", "separation", "kiln", "reactor", "autoclave",
    "physical quantity", "quantity", "density", "hardness", "viscosity",
    "porosity", "strength", "process of", "beneficiation", "electrolyte",
    "ceramic", "compound",
)


def _domain_score(desc: str | None) -> int:
    d = (desc or "").lower()
    return sum(1 for k in _DOMAIN_KW if k in d)


@dataclass
class ResolvedAnchor:
    en_term: str
    label: str
    qid: str
    wd_label: str
    ru_label: str
    description: str
    needs_review: bool


def resolve_concepts(concepts: list[tuple[str, str]],
                     harvester: "WikidataHarvester",
                     min_sites: int = 3) -> list[ResolvedAnchor]:
    """Map curated ``(en_term, label)`` pairs to Wikidata QIDs via SPARQL.

    Ranking weighs **domain relevance first, notability second**: among exact
    EN-label matches (each carrying a RU label), candidates whose description
    reads mining/metallurgy win, ties broken by sitelink count. This picks the
    domain sense of a homonym even when a non-domain sense is far more notable
    (``matte`` → штейн, not picture-framing; ``flotation`` → флотация, not IPO).

    Flags ``needs_review=True`` when: no hit; no candidate matches any domain
    keyword (so the pick rests on notability alone); or the chosen item is
    thinly linked (``< min_sites``). Reviewed rows get a human-confirmed QID.
    """
    terms = [t for t, _ in concepts]
    cand_map = harvester.resolve_by_label(terms)
    out: list[ResolvedAnchor] = []
    for term, label in concepts:
        cands = cand_map.get(term.casefold(), [])
        if not cands:
            out.append(ResolvedAnchor(term, label, "?", "", "", "NO-HIT", True))
            continue
        # domain relevance first, then sitelinks
        ranked = sorted(cands, key=lambda c: (-_domain_score(c["desc"]),
                                              -c["sites"]))
        top = ranked[0]
        any_domain = _domain_score(top["desc"]) > 0
        needs_review = (not any_domain) or top["sites"] < min_sites
        desc = f"{top['desc'][:50]!r} sites={top['sites']} dom={_domain_score(top['desc'])}"
        out.append(ResolvedAnchor(
            en_term=term, label=label, qid=top["qid"],
            wd_label="", ru_label=top["ru"], description=desc,
            needs_review=needs_review))
    return out


def _esc(s: str) -> str:
    """Escape a string for a SPARQL double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _dedup(values: list[str]) -> list[str]:
    """Drop empties + case-insensitive duplicates, order-stable."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    return out


# Sub/superscript digits used in isotope notation (₂₈Ni, ²⁸Ni) — a noise marker.
_SUBSUP = "".join(chr(c) for c in range(0x2080, 0x208A))
_SUBSUP += "".join(chr(c) for c in (0x2070, 0x00B9, 0x00B2, 0x00B3,
                                    *range(0x2074, 0x207A)))


def _term_like(s: str) -> bool:
    """True if ``s`` reads like a real term rather than catalogue noise.

    Rejects ASCII digits (``element 28``, brand codes ``Paragard T 380A``),
    isotope sub/superscripts (``₂₈Ni``), and over-long strings. Short pure-letter
    symbols (``Ni``, ``Cu``) and multiword names pass. Applied to *aliases* only;
    the canonical label is always kept.
    """
    if not s or len(s) > 40:
        return False
    if any(ch.isdigit() for ch in s):
        return False
    if any(ch in _SUBSUP for ch in s):
        return False
    return True


def _clean_forms(forms: list[str]) -> list[str]:
    """Keep canonical form (index 0); filter aliases to term-like surface forms."""
    if not forms:
        return []
    head = forms[0]
    return _dedup([head] + [f for f in forms[1:] if _term_like(f)])


def load_anchor_file(path: str | Path) -> list[tuple[str, str]]:
    """Read committed ``qid <TAB> en <TAB> ru <TAB> label`` anchor rows.

    Returns ``[(qid, label)]``; en/ru columns are documentation only (the live
    labels+aliases are fetched fresh so they never drift from Wikidata).
    """
    path = Path(path)
    if not path.exists():
        logger.warning("Anchor file not found: %s", path)
        return []
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        qid, _en, _ru, label = parts[0], parts[1], parts[2], parts[3]
        if qid.startswith("Q"):
            out.append((qid.strip(), label.strip().upper()))
    return out


def concepts_to_glossary_rows(concepts: list[WdConcept]) -> list[dict]:
    """Flatten concepts into seed-loader JSONL rows (term, label, lang, qid)."""
    rows: list[dict] = []
    for c in concepts:
        for term, lang in c.surface_forms():
            rows.append({"term": term, "label": c.label, "lang": lang,
                         "qid": c.qid})
    return rows


def write_glossary(concepts: list[WdConcept],
                   out_path: str | Path = "data/seed/wikidata_glossary.jsonl",
                   pairs_path: str | Path = "data/wikidata_must_link.json"
                   ) -> None:
    """Persist the seed glossary (JSONL) + must-link pairs (JSON)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in concepts_to_glossary_rows(concepts):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    pairs: list[list[str]] = []
    for c in concepts:
        pairs.extend([a, b] for a, b in c.must_link_pairs())
    Path(pairs_path).write_text(
        json.dumps(pairs, ensure_ascii=False, indent=0), encoding="utf-8")
    logger.info("Wrote %d glossary rows + %d must-link pairs",
                sum(len(c.surface_forms()) for c in concepts), len(pairs))


def load_must_link(path: str | Path = "data/wikidata_must_link.json"
                   ) -> list[tuple[str, str]]:
    """Read the must-link pairs file back as tuples (for the pipeline)."""
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    # Tolerate malformed rows (not exactly a 2-list) rather than raising.
    return [(row[0], row[1]) for row in data
            if isinstance(row, (list, tuple)) and len(row) == 2 and row[0] and row[1]]


__all__ = [
    "WdConcept", "WikidataHarvester", "ResolvedAnchor", "resolve_concepts",
    "load_anchor_file", "concepts_to_glossary_rows", "write_glossary",
    "load_must_link",
]
