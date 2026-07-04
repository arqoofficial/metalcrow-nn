"""Wikipedia interwiki (langlinks) harvest → the specialized long tail.

Companion to :mod:`term_dict.wikidata`. Wikidata is strong on elements, metals,
minerals and general processes, but sparse on specialized mining-metallurgy
unit operations and equipment — *electrowinning*, *flash smelting*,
*jaw crusher* have no RU-labelled Wikidata item, so the Wikidata harvest can't
pair them. Many of those concepts DO have paired EN/RU Wikipedia articles,
though, and Wikipedia's ``langlinks`` API returns the Russian interlanguage
link of a named English article directly.

Because we name the *exact* article (not a label search), there is no homonym
problem: the RU langlink of ``Flash smelting`` is ground-truth ``Взвешенная
плавка``. Each resolved pair feeds the clusterer as a **must-link** edge, same
contract as the Wikidata harvest (:class:`term_dict.wikidata.WdConcept`).

Reuses the tested, cached, rate-limited HTTP client from the Wikidata harvester
(one polite client, one on-disk cache under ``data/wikidata_cache/``), so reruns
are offline and reproducible. Only public Wikipedia is queried — never the
untrusted corpus — so this is safe to run without the parsing gate.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
from pathlib import Path

from .wikidata import WdConcept, WikidataHarvester, write_glossary

logger = logging.getLogger(__name__)

WP_API = "https://en.wikipedia.org/w/api.php"
DEFAULT_TERMS = Path("data/wikipedia_terms.tsv")
DEFAULT_GLOSSARY = Path("data/seed/wikipedia_glossary.jsonl")
DEFAULT_PAIRS = Path("data/wikipedia_must_link.json")


def load_term_file(path: str | Path) -> list[tuple[str, str]]:
    """Read ``en_title <TAB> label [<TAB> note]`` rows → ``[(title, label)]``.

    Blank lines and ``#`` comments are ignored; a third ``note`` column (and any
    beyond it) is documentation only. The label is upper-cased.
    """
    path = Path(path)
    if not path.exists():
        logger.warning("Wikipedia term file not found: %s", path)
        return []
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        title, label = parts[0].strip(), parts[1].strip().upper()
        if title:
            out.append((title, label))
    return out


def _follow(title: str, chain: dict[str, str], cap: int = 6) -> str:
    """Walk a from→to redirect/normalization chain to the final article title."""
    seen: set[str] = set()
    while title in chain and title not in seen and cap > 0:
        seen.add(title)
        title = chain[title]
        cap -= 1
    return title


def _langlink_value(ll: dict) -> str:
    """Read a langlink target title across API format variants (``*`` vs title)."""
    return ll.get("*") or ll.get("title") or ll.get("value") or ""


class WikipediaHarvester:
    """Resolve named EN Wikipedia articles to their RU interlanguage link."""

    def __init__(self, http: WikidataHarvester | None = None) -> None:
        # Reuse the Wikidata harvester purely as a polite cached JSON client.
        self.http = http or WikidataHarvester()

    def fetch_langlinks(self, titles: list[str],
                        lang: str = "ru") -> dict[str, str]:
        """Return ``{input_title: langlink_title}`` for titles that have one.

        Batches titles into ``langlinks`` queries and traces each input through
        the API's ``normalized`` + ``redirects`` maps to the final page, so a
        redirect or case-fold never loses the attribution. Titles whose article
        is missing or has no ``lang`` link are simply absent from the result.
        """
        resolved: dict[str, str] = {}
        for i in range(0, len(titles), 40):
            batch = titles[i:i + 40]
            params = {
                "action": "query", "format": "json", "prop": "langlinks",
                "lllang": lang, "lllimit": "max", "redirects": "1",
                "titles": "|".join(batch),
            }
            url = WP_API + "?" + urllib.parse.urlencode(params)
            # Content-address on the batch so editing the term list never
            # returns a stale cached response under a positional key.
            digest = hashlib.sha1("|".join(batch).encode("utf-8")).hexdigest()[:10]
            data = self.http.get_json(url, f"wp_langlinks_{lang}_{digest}")
            q = data.get("query", {})

            chain: dict[str, str] = {}
            for m in q.get("normalized", []):
                chain[m["from"]] = m["to"]
            for m in q.get("redirects", []):
                chain[m["from"]] = m["to"]

            title_ru: dict[str, str] = {}
            for page in q.get("pages", {}).values():
                lls = page.get("langlinks") or []
                if lls:
                    val = _langlink_value(lls[0])
                    if val:
                        title_ru[page.get("title", "")] = val

            for t in batch:
                ru = title_ru.get(_follow(t, chain))
                if ru:
                    resolved[t] = ru
        return resolved

    def build_concepts(self, terms_path: str | Path = DEFAULT_TERMS,
                       lang: str = "ru") -> list[WdConcept]:
        """Named EN articles → :class:`WdConcept` list (one EN↔RU pair each).

        Articles with no ``lang`` langlink are skipped with a warning so the
        curated list can be generous — the API filters it down to real pairs.
        """
        pairs = load_term_file(terms_path)
        titles = [t for t, _ in pairs]
        links = self.fetch_langlinks(titles, lang=lang)
        concepts: list[WdConcept] = []
        for title, label in pairs:
            ru = links.get(title)
            if not ru:
                logger.warning("No %s langlink for %r — skipped", lang, title)
                continue
            concepts.append(WdConcept(
                qid=f"WP:{title}", label=label,
                canonical_en=title, canonical_ru=ru,
                en=[title], ru=[ru]))
        logger.info("Wikipedia harvest: %d/%d titles resolved to a %s pair",
                    len(concepts), len(pairs), lang)
        return concepts


def harvest(terms_path: str | Path = DEFAULT_TERMS,
            glossary_path: str | Path = DEFAULT_GLOSSARY,
            pairs_path: str | Path = DEFAULT_PAIRS,
            http: WikidataHarvester | None = None) -> list[WdConcept]:
    """Run the Wikipedia harvest end-to-end and persist glossary + pairs."""
    concepts = WikipediaHarvester(http=http).build_concepts(terms_path)
    write_glossary(concepts, out_path=glossary_path, pairs_path=pairs_path)
    return concepts


__all__ = [
    "WikipediaHarvester", "harvest", "load_term_file",
    "DEFAULT_TERMS", "DEFAULT_GLOSSARY", "DEFAULT_PAIRS",
]
