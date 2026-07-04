"""Cross-lingual synonym clustering with LaBSE.

Groups surface terms that denote the same concept across RU and EN. LaBSE
embeds a RU term and its EN translation close together *by meaning*, so we do
not need an explicit link in the source text (the hard case OSN called out).

Pipeline:
1. Embed every unique term with a cross-lingual sentence encoder (LaBSE).
2. Build a similarity graph: edge iff cosine >= ``sim_threshold``.
3. Add cheap non-semantic edges for loanwords: transliteration + normalized
   edit distance (catches «электролизёр» ↔ "electrolyzer" style pairs that a
   purely semantic encoder can still get right, but this hardens recall).
4. Connected components → candidate synonym clusters.
5. Each cluster gets a canonical label + a per-edge confidence so the LLM /
   human validation step only looks at borderline merges.

The encoder is loaded lazily so importing this module (and running the
Schwartz-Hearst part) costs nothing when embeddings are not needed.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from .config import AUTO_ACCEPT_THRESHOLD, DEFAULT_ENCODER, DEFAULT_SIM_THRESHOLD

logger = logging.getLogger(__name__)

# Tokens this short (element symbols, 2-letter acronyms) only join concepts via
# explicit must-link edges, never fuzzy similarity/lexical edges.
MIN_EMBED_LEN = 2

# Complete-linkage backstop: a fuzzy (similarity/lexical) merge never grows a
# component past this size. Real cross-lingual synonym sets are small; a big
# component is the single-linkage-chaining failure mode (the 301-element blob).
MAX_CLUSTER_SIZE = 25

# --- Cyrillic -> Latin transliteration (GOST-ish, lossy but fine for loanword
# matching). Only used as a *secondary* recall signal, never authoritative. ---
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(text: str) -> str:
    """Cyrillic → Latin, lower-cased, non-alnum stripped (loanword key)."""
    out = []
    for ch in text.lower():
        out.append(_CYR_TO_LAT.get(ch, ch))
    latin = "".join(out)
    return "".join(c for c in latin if c.isalnum())


def _norm(text: str) -> str:
    """NFKC-normalize + casefold for stable comparison keys."""
    return unicodedata.normalize("NFKC", text).casefold().strip()


def edit_distance_ratio(a: str, b: str) -> float:
    """Normalized similarity in [0,1] from Levenshtein distance.

    Uses python-Levenshtein when available, else a small DP fallback so the
    module never hard-fails on a missing optional dep.
    """
    if not a and not b:
        return 1.0
    try:
        import Levenshtein  # type: ignore

        dist = Levenshtein.distance(a, b)
    except Exception:  # pragma: no cover - fallback path
        dist = _levenshtein(a, b)
    return 1.0 - dist / max(len(a), len(b), 1)


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass
class SynonymCluster:
    """A group of surface terms judged to denote one concept."""

    cluster_id: int
    terms: list[str]
    canonical: str
    label: str = "UNKNOWN"
    # Weakest intra-cluster edge similarity; low → send to LLM/human review.
    min_edge_sim: float = 1.0
    borderline: bool = False
    members: dict[str, str] = field(default_factory=dict)  # term -> source label
    # Realized pairwise links that formed this cluster: (term_a, term_b,
    # confidence, method). method ∈ {schwartz_hearst, wikidata, wikipedia,
    # contract, embedding, lexical}. Feeds entity_same_as(confidence, method)
    # per-pair — never a star-to-canonical edge with a fabricated method.
    edges: list[tuple[str, str, float, str]] = field(default_factory=list)


class SynonymClusterer:
    """Embed terms and cluster them into cross-lingual synonym sets."""

    def __init__(
        self,
        encoder_name: str = DEFAULT_ENCODER,
        sim_threshold: float = DEFAULT_SIM_THRESHOLD,
        auto_accept: float = AUTO_ACCEPT_THRESHOLD,
        use_lexical_edges: bool = True,
    ) -> None:
        self.encoder_name = encoder_name
        self.sim_threshold = sim_threshold
        self.auto_accept = auto_accept
        self.use_lexical_edges = use_lexical_edges
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading cross-lingual encoder %s (CPU)", self.encoder_name)
            self._model = SentenceTransformer(self.encoder_name)
        return self._model

    def embed(self, terms: list[str]) -> np.ndarray:
        """L2-normalized embeddings so dot product == cosine similarity."""
        model = self._load_model()
        emb = model.encode(
            terms,
            batch_size=64,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return emb.astype(np.float32)

    def _lexical_edges(self, terms: list[str]) -> list[tuple[int, int]]:
        """Loanword edges via transliteration + edit distance (cheap, recall)."""
        keys = [transliterate(t) for t in terms]
        edges: list[tuple[int, int]] = []
        for i in range(len(terms)):
            if not keys[i]:
                continue
            for j in range(i + 1, len(terms)):
                if not keys[j]:
                    continue
                if edit_distance_ratio(keys[i], keys[j]) >= 0.85:
                    edges.append((i, j))
        return edges

    def cluster(
        self,
        terms: list[str],
        labels: dict[str, str] | None = None,
        must_link: list[tuple] | None = None,
    ) -> list[SynonymCluster]:
        """Return synonym clusters over ``terms`` (must-link + complete-linkage).

        ``must_link`` entries are ``(a, b)`` or ``(a, b, method)`` (default
        method ``"must_link"``); they are joined unconditionally and define the
        ground-truth concept groups.

        Fuzzy (similarity/lexical) edges merge two components only under
        *complete linkage* — every cross-pair must clear ``sim_threshold`` — so a
        chain of individually-strong edges can never fuse a large blob of
        unrelated terms (the single-linkage failure that produced a 301-member
        "concept"). Two distinct must-link groups are additionally forbidden from
        fuzzy-fusing with each other (they are already asserted-distinct
        concepts, e.g. two different elements), and the existing cannot-link
        (conflicting known labels) and short-token guards still apply.
        """
        labels = labels or {}
        must_link = must_link or []
        uniq = list(dict.fromkeys(t for t in terms if t and t.strip()))
        if not uniq:
            return []
        n = len(uniq)
        # Exact-match only: a case-insensitive fallback is unsafe here because
        # short symbols collide under casefold (tensile-strength alias "TS" vs
        # tennessine's symbol "Ts"), which would force a cross-label merge. A
        # case-only-variant must-link pair is instead dropped (fail-safe); the
        # builder emits seed surface forms and pairs in matching case anyway.
        pos = {t: k for k, t in enumerate(uniq)}

        def _pos(t: str):
            return pos.get(t)

        emb = self.embed(uniq)
        sim = emb @ emb.T  # cosine, since rows are unit-norm

        parent = list(range(n))
        # Live member index-list per root, for complete-linkage cross-checks.
        comp_members: list[list[int]] = [[i] for i in range(n)]
        # Known (non-UNKNOWN) labels per component (cannot-link guard).
        comp_labels: list[set[str]] = [set() for _ in range(n)]
        for idx, term in enumerate(uniq):
            lab = labels.get(term)
            if lab and lab != "UNKNOWN":
                comp_labels[idx].add(lab)
        # Ground-group ids per component: which must-link concept groups a
        # component already belongs to. A fuzzy edge may not join two components
        # that each carry a *different* ground group (asserted-distinct concepts).
        comp_ground: list[set[int]] = [set() for _ in range(n)]

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def raw_union(ra: int, rb: int) -> int:
            """Merge root rb into ra (caller guarantees ra != rb, guards passed)."""
            parent[rb] = ra
            comp_members[ra].extend(comp_members[rb])
            comp_labels[ra] |= comp_labels[rb]
            comp_ground[ra] |= comp_ground[rb]
            return ra

        short = [len(t.strip()) <= MIN_EMBED_LEN for t in uniq]
        realized: list[tuple[int, int, float, str]] = []  # (i, j, weight, method)

        # --- 1. Must-link first: ground-truth groups (force, bypass guards). ---
        norm_ml: list[tuple[int, int, str]] = []
        for entry in must_link:
            a, b = entry[0], entry[1]
            method = entry[2] if len(entry) > 2 else "must_link"
            ia, ib = _pos(a), _pos(b)
            if ia is not None and ib is not None and ia != ib:
                norm_ml.append((ia, ib, method))
        for gid, (ia, ib, method) in enumerate(norm_ml):
            comp_ground[find(ia)].add(gid)
            comp_ground[find(ib)].add(gid)
        for gid, (ia, ib, method) in enumerate(norm_ml):
            ra, rb = find(ia), find(ib)
            if ra != rb:
                raw_union(ra, rb)
            realized.append((ia, ib, 1.0, method))

        def fuzzy_ok(ra: int, rb: int, exempt: tuple[int, int] | None = None) -> bool:
            if ra == rb:
                return False
            if len(comp_labels[ra] | comp_labels[rb]) > 1:
                return False  # cannot-link: conflicting known labels
            if len(comp_ground[ra]) and len(comp_ground[rb]) \
                    and comp_ground[ra] != comp_ground[rb]:
                return False  # two asserted-distinct concept groups
            if len(comp_members[ra]) + len(comp_members[rb]) > MAX_CLUSTER_SIZE:
                return False  # size backstop
            # Complete linkage: EVERY cross-pair must clear the threshold — except
            # the ``exempt`` pair itself. A lexical (loanword) edge is vouched for
            # by transliteration+edit-distance, precisely because its *embedding*
            # cosine is below threshold; exempting only that one pair keeps its
            # recall while the remaining cross-pairs still block chaining.
            ex = frozenset(exempt) if exempt else None
            for x in comp_members[ra]:
                for y in comp_members[rb]:
                    if ex is not None and frozenset((x, y)) == ex:
                        continue
                    if sim[x, y] < self.sim_threshold:
                        return False
            return True

        # --- 2. Similarity edges, strongest-first, complete-linkage merges. ---
        cand_edges = [
            (float(sim[i, j]), i, j)
            for i in range(n) for j in range(i + 1, n)
            if sim[i, j] >= self.sim_threshold and not short[i] and not short[j]
        ]
        cand_edges.sort(reverse=True)
        for w, i, j in cand_edges:
            ra, rb = find(i), find(j)
            if fuzzy_ok(ra, rb):
                raw_union(ra, rb)
                realized.append((i, j, w, "embedding"))

        # --- 3. Lexical loanword edges (secondary recall), same guards. ---
        if self.use_lexical_edges:
            for i, j in self._lexical_edges(uniq):
                if short[i] or short[j]:
                    continue  # e.g. transliterate("Eu")==transliterate("ЭУ")
                ra, rb = find(i), find(j)
                if fuzzy_ok(ra, rb, exempt=(i, j)):
                    raw_union(ra, rb)
                    # Confidence reflects the lexical floor (0.85), not the low
                    # embedding cosine that this edge deliberately bypasses.
                    realized.append((i, j, round(max(float(sim[i, j]), 0.85), 4),
                                     "lexical"))

        # --- 4. Materialize clusters. ---
        comps: dict[int, list[int]] = {}
        for idx in range(n):
            comps.setdefault(find(idx), []).append(idx)

        edges_by_root: dict[int, list[tuple[int, int, float, str]]] = {}
        for i, j, w, method in realized:
            edges_by_root.setdefault(find(i), []).append((i, j, w, method))

        clusters: list[SynonymCluster] = []
        for cid, (root, members) in enumerate(sorted(comps.items())):
            terms_in = [uniq[m] for m in members]
            root_edges = edges_by_root.get(root, [])
            min_sim = min((w for _, _, w, _ in root_edges), default=1.0)
            canonical = self._pick_canonical(terms_in, labels)
            cl_label = self._pick_label(terms_in, labels)
            # A cluster spanning >1 *known* label is inherently suspect — this
            # only happens when a must-link forced a conflicting-label merge
            # (e.g. an ambiguous symbol like "At" bridging astatine↔elongation).
            # Flag it for human review rather than ship it silently.
            known_labels = {labels[t] for t in terms_in
                            if labels.get(t, "UNKNOWN") != "UNKNOWN"}
            multi_label = len(known_labels) > 1
            clusters.append(SynonymCluster(
                cluster_id=cid,
                terms=sorted(terms_in),
                canonical=canonical,
                label=cl_label,
                min_edge_sim=round(min_sim, 4),
                borderline=(len(terms_in) > 1 and min_sim < self.auto_accept)
                           or multi_label,
                members={t: labels.get(t, cl_label) for t in terms_in},
                edges=[(uniq[i], uniq[j], round(w, 4), method)
                       for i, j, w, method in root_edges],
            ))
        logger.info("Clustered %d terms into %d synonym sets (%d borderline)",
                    n, len(clusters), sum(c.borderline for c in clusters))
        return clusters

    @staticmethod
    def _pick_canonical(terms: list[str], labels: dict[str, str]) -> str:
        """Prefer the shortest Latin-script term, else the shortest overall.

        A stable, deterministic pick; the real canonical name can be refined
        during validation. Latin bias keeps map keys ASCII-friendly for the
        graph store while all surface forms remain searchable.
        """
        def is_latin(t: str) -> bool:
            return all(ord(c) < 128 or not c.isalpha() for c in t)

        def is_acronymish(t: str) -> bool:
            # all-caps short token (EW, POX) — a poor canonical *name* even
            # though it is a fine surface form; element symbols like "Ni" are
            # mixed-case so this leaves them eligible.
            return t.isupper() and 2 <= len(t) <= 5

        latin = [t for t in terms if is_latin(t)]
        pool = latin or terms
        spelled = [t for t in pool if not is_acronymish(t)]
        pool = spelled or pool
        return min(pool, key=lambda t: (len(t), t.lower()))

    @staticmethod
    def _pick_label(terms: list[str], labels: dict[str, str]) -> str:
        """Majority known label among cluster members, else UNKNOWN."""
        seen = [labels[t] for t in terms if t in labels and labels[t] != "UNKNOWN"]
        if not seen:
            return "UNKNOWN"
        return max(set(seen), key=seen.count)


__all__ = [
    "SynonymCluster",
    "SynonymClusterer",
    "transliterate",
    "edit_distance_ratio",
]
