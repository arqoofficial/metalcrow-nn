"""Wikipedia langlinks harvester tests — offline, stubbed HTTP client.

Feeds canned ``langlinks`` API responses so attribution through the
``normalized``/``redirects`` maps and the missing-langlink skip are tested
deterministically without touching the network.
"""

from term_dict import wikipedia
from term_dict.wikipedia import WikipediaHarvester, _follow, load_term_file


class _StubHttp:
    """Stand-in for WikidataHarvester: returns a fixed langlinks payload."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get_json(self, url, cache_key):
        self.calls.append((url, cache_key))
        return self._payload


# One batch covering: a plain hit, a redirect hit, a normalized (case) hit,
# a missing article, and an article with no RU langlink.
_PAYLOAD = {
    "query": {
        "normalized": [{"from": "ball mill", "to": "Ball mill"}],
        "redirects": [{"from": "Froth flotation", "to": "Flotation"}],
        "pages": {
            "1": {"title": "Flash smelting",
                  "langlinks": [{"lang": "ru", "*": "Взвешенная плавка"}]},
            "2": {"title": "Flotation",
                  "langlinks": [{"lang": "ru", "*": "Флотация"}]},
            "3": {"title": "Ball mill",
                  "langlinks": [{"lang": "ru", "*": "Шаровая мельница"}]},
            "4": {"title": "Nonexistent article", "missing": ""},
            "5": {"title": "Obscure thing", "langlinks": []},
        },
    }
}


def test_follow_traces_redirect_chain():
    chain = {"a": "b", "b": "c"}
    assert _follow("a", chain) == "c"
    assert _follow("x", chain) == "x"  # not in chain → identity


def test_follow_is_cycle_safe():
    assert _follow("a", {"a": "b", "b": "a"}) in {"a", "b"}  # terminates


def test_fetch_langlinks_resolves_plain_redirect_and_normalized():
    h = WikipediaHarvester(http=_StubHttp(_PAYLOAD))
    got = h.fetch_langlinks(
        ["Flash smelting", "Froth flotation", "ball mill",
         "Nonexistent article", "Obscure thing"])
    assert got["Flash smelting"] == "Взвешенная плавка"   # direct
    assert got["Froth flotation"] == "Флотация"           # via redirect
    assert got["ball mill"] == "Шаровая мельница"         # via normalized
    assert "Nonexistent article" not in got               # missing → skipped
    assert "Obscure thing" not in got                     # no ru link → skipped


def test_build_concepts_skips_unresolved(tmp_path):
    terms = tmp_path / "terms.tsv"
    terms.write_text(
        "# comment\n"
        "Flash smelting\tPROCESS\t# note\n"
        "Ball mill\tEQUIPMENT\n"
        "Obscure thing\tEQUIPMENT\n",  # no ru link → dropped
        encoding="utf-8")
    h = WikipediaHarvester(http=_StubHttp(_PAYLOAD))
    concepts = h.build_concepts(terms)
    by_en = {c.canonical_en: c for c in concepts}
    assert set(by_en) == {"Flash smelting", "Ball mill"}
    fs = by_en["Flash smelting"]
    assert fs.label == "PROCESS"
    assert fs.canonical_ru == "Взвешенная плавка"
    assert fs.qid == "WP:Flash smelting"
    # A ground-truth EN↔RU must-link pair is produced.
    assert fs.must_link_pairs() == [("Flash smelting", "Взвешенная плавка")]


def test_load_term_file_parses_and_skips_comments(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_text("# header\n\nJaw crusher\tequipment\t# ru: щёковая\n"
                 "Bare\n",  # too few columns → skipped
                 encoding="utf-8")
    rows = load_term_file(p)
    assert rows == [("Jaw crusher", "EQUIPMENT")]


def test_batch_cache_key_is_content_addressed():
    # Different title sets must not collide on the same cache key.
    h = WikipediaHarvester(http=_StubHttp(_PAYLOAD))
    h.fetch_langlinks(["Ball mill"])
    h.fetch_langlinks(["Flash smelting"])
    keys = [c[1] for c in h.http.calls]
    assert len(set(keys)) == 2
