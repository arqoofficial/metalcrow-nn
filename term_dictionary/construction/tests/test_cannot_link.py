"""Cannot-link / must-link constraint tests using a stub encoder.

Avoids the LaBSE download by injecting controlled embeddings so the graph
logic (thresholding, cannot-link guard, must-link override) is tested
deterministically.
"""

import numpy as np

from term_dict.synonym_cluster import SynonymClusterer


class _StubClusterer(SynonymClusterer):
    """Return caller-supplied unit vectors instead of encoding with a model."""

    def __init__(self, vectors, **kw):
        super().__init__(use_lexical_edges=False, **kw)
        self._vectors = vectors

    def embed(self, terms):
        mat = np.array([self._vectors[t] for t in terms], dtype=np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        return mat


def _cluster_of(clusters, term):
    for c in clusters:
        if term in c.terms:
            return c
    raise AssertionError(f"{term} not clustered")


def test_cannot_link_blocks_cross_label_merge():
    # Two nearly-identical vectors but conflicting known labels -> must NOT merge.
    vecs = {"electrowinning": [1.0, 0.0], "electrolyzer": [0.99, 0.01]}
    labels = {"electrowinning": "PROCESS", "electrolyzer": "EQUIPMENT"}
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(
        list(vecs), labels)
    assert _cluster_of(clusters, "electrowinning") is not _cluster_of(
        clusters, "electrolyzer")


def test_same_label_still_merges():
    vecs = {"nickel": [1.0, 0.0], "никель": [0.98, 0.02]}
    labels = {"nickel": "MATERIAL", "никель": "MATERIAL"}
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(list(vecs), labels)
    assert _cluster_of(clusters, "nickel") is _cluster_of(clusters, "никель")


def test_must_link_overrides_cannot_link():
    # Even with conflicting labels, an explicit must-link pair is forced.
    vecs = {"POX": [0.0, 1.0], "pressure oxidation": [1.0, 0.0]}
    labels = {"POX": "EQUIPMENT", "pressure oxidation": "PROCESS"}
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(
        list(vecs), labels, must_link=[("POX", "pressure oxidation")])
    assert _cluster_of(clusters, "POX") is _cluster_of(clusters, "pressure oxidation")


def test_unknown_bridge_does_not_chain_conflicting_labels():
    # A ~ bridge ~ C all similar, but A and C carry conflicting known labels.
    # Component-level guard must keep A and C apart.
    vecs = {"A": [1.0, 0.0], "bridge": [0.97, 0.03], "C": [0.95, 0.05]}
    labels = {"A": "PROCESS", "C": "EQUIPMENT"}  # bridge is UNKNOWN
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(list(vecs), labels)
    assert _cluster_of(clusters, "A") is not _cluster_of(clusters, "C")


def test_short_symbols_do_not_chain_via_similarity():
    # Element symbols "Eu" and "EW" embed near each other (surface noise) but
    # denote unrelated concepts. The short-token guard must stop the fuzzy edge;
    # each symbol only joins its concept via must-link.
    vecs = {"Eu": [1.0, 0.0], "EW": [0.99, 0.01],
            "europium": [0.2, 0.98], "electrowinning": [-0.2, 0.97]}
    labels = {"europium": "MATERIAL", "electrowinning": "PROCESS"}
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(
        list(vecs), labels,
        must_link=[("europium", "Eu"), ("electrowinning", "EW")])
    # Eu rides with europium, EW with electrowinning — never fused together.
    assert _cluster_of(clusters, "Eu") is _cluster_of(clusters, "europium")
    assert _cluster_of(clusters, "EW") is _cluster_of(clusters, "electrowinning")
    assert _cluster_of(clusters, "Eu") is not _cluster_of(clusters, "EW")


def test_complete_linkage_breaks_single_linkage_chain():
    # A—B—C chained by strong pairwise edges, but A and C are NOT similar.
    # Single-linkage (old behaviour) fused all three; complete-linkage must not:
    # C only joins if it clears the threshold against EVERY member of {A,B}.
    vecs = {"aaa": [1.0, 0.00], "bbb": [0.92, 0.39], "ccc": [0.55, 0.84]}
    # sim(aaa,bbb)=0.92, sim(bbb,ccc)≈0.83 (both ≥0.8), sim(aaa,ccc)≈0.55 (<0.8).
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(list(vecs), {})
    assert _cluster_of(clusters, "aaa") is _cluster_of(clusters, "bbb")
    assert _cluster_of(clusters, "ccc") is not _cluster_of(clusters, "aaa")


def test_distinct_mustlink_groups_do_not_fuzzy_fuse():
    # Two elements, each a ground-truth must-link group (symbol↔name). Their
    # names embed similarly (LaBSE puts element names near each other) — but two
    # asserted-distinct concepts must never fuzzy-fuse into one "element blob".
    vecs = {"nickel": [1.0, 0.0], "Ni": [0.99, 0.02],
            "cobalt": [0.98, 0.03], "Co": [0.97, 0.04]}
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(
        list(vecs), {"nickel": "MATERIAL", "cobalt": "MATERIAL"},
        must_link=[("nickel", "Ni", "wikidata"), ("cobalt", "Co", "wikidata")])
    assert _cluster_of(clusters, "nickel") is not _cluster_of(clusters, "cobalt")
    assert _cluster_of(clusters, "nickel") is _cluster_of(clusters, "Ni")


class _LexStub(SynonymClusterer):
    """Stub encoder WITH lexical edges enabled (loanword recall path)."""

    def __init__(self, vectors, **kw):
        super().__init__(use_lexical_edges=True, **kw)
        self._vectors = vectors

    def embed(self, terms):
        mat = np.array([self._vectors[t] for t in terms], dtype=np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        return mat


def test_lexical_edge_merges_loanword_encoder_misses():
    # Embedding cosine is ~0 (encoder misses the loanword), but transliteration
    # makes them lexically identical. The lexical pass must still merge them —
    # this was dead code before the exempt-the-direct-pair fix.
    vecs = {"электролизер": [1.0, 0.0], "elektrolizer": [0.0, 1.0]}
    clusters = _LexStub(vecs, sim_threshold=0.8).cluster(list(vecs), {})
    assert _cluster_of(clusters, "электролизер") is _cluster_of(clusters, "elektrolizer")
    methods = {e[3] for c in clusters for e in c.edges}
    assert "lexical" in methods


def test_multi_label_mustlink_merge_is_flagged():
    # An ambiguous token "At" must-linked into both astatine (MATERIAL) and
    # elongation (PROPERTY) forces a cross-label merge — it must be flagged
    # needs_review rather than shipped silently.
    vecs = {"astatine": [1.0, 0.0], "At": [0.5, 0.5], "elongation": [0.0, 1.0]}
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(
        list(vecs), {"astatine": "MATERIAL", "elongation": "PROPERTY"},
        must_link=[("astatine", "At", "wikidata"), ("elongation", "At", "contract")])
    c = _cluster_of(clusters, "At")
    assert c.borderline is True  # multi-label → needs_review


def test_edges_carry_method_and_confidence():
    # Realized links expose (a, b, confidence, method) for entity_same_as.
    vecs = {"pressure oxidation": [1.0, 0.0], "POX": [0.0, 1.0],
            "автоклав": [0.95, 0.05], "autoclave": [0.9, 0.04]}
    clusters = _StubClusterer(vecs, sim_threshold=0.8).cluster(
        list(vecs), {"автоклав": "EQUIPMENT", "autoclave": "EQUIPMENT"},
        must_link=[("pressure oxidation", "POX", "schwartz_hearst")])
    pox = _cluster_of(clusters, "POX")
    methods = {e[3] for e in pox.edges}
    assert "schwartz_hearst" in methods
    assert all(0.0 <= e[2] <= 1.0 for e in pox.edges)
    auto = _cluster_of(clusters, "автоклав")
    assert {e[3] for e in auto.edges} <= {"embedding", "lexical"}
