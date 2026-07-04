#!/usr/bin/env python3
"""Anna's Archive SciDB per-DOI coverage checker.

Anna's Archive SciDB aggregates Nexus + Sci-Hub + LibGen + Z-Library, so a SciDB
hit is the practical superset of "available in STC/Nexus". This measures, for a
DOI list, how many papers are retrievable — and (optionally) downloads them.

The `.gl` mirror is reachable from this VM without a VPN change (the .org/.se
domains are IPv6-only/geo-DNS here). No account/API key needed for SciDB lookups
or the extracted fast-download PDF link.

Usage:
    # one DOI list, print summary + per-DOI JSONL
    python scidb_coverage.py dois.txt
    # multiple datasets, summary table only
    python scidb_coverage.py ds1.txt ds2.txt --quiet
    # also download hit PDFs into ./pdfs/
    python scidb_coverage.py dois.txt --download pdfs/

A DOI list is a text file with one DOI per line (blank lines / `#` comments
ignored); bare `10.x/...`, `doi:10.x/...`, or `https://doi.org/10.x/...` all work.
"""
import argparse
import json
import os
import re
import sys
import time

import requests

MIRROR = os.environ.get("SCIDB_MIRROR", "https://annas-archive.gl")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
PDF_RE = re.compile(r"https?://[^\s\"'<>]+\.pdf[^\s\"'<>]*")


def normalize_doi(raw: str) -> str:
    """Strip scheme/resolver host and a leading ``doi:`` so we pass a bare DOI."""
    s = raw.strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:", "", s, flags=re.I)
    return s.strip()


def scidb_lookup(doi: str, session: requests.Session, timeout: int = 30) -> dict:
    """Return {doi, status: hit|miss|err, pdf_url, title}.

    HIT  = SciDB serves the paper page (title is the paper) with a direct PDF link.
    MISS = SciDB falls through to its search page (title contains '- Search -').
    """
    url = f"{MIRROR}/scidb/{doi}"
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout)
    except Exception as exc:  # network/timeout — caller decides retry policy
        return {"doi": doi, "status": "err", "pdf_url": None, "title": str(exc)[:80]}
    title_m = TITLE_RE.search(r.text)
    title = (title_m.group(1).strip() if title_m else "")[:80]
    if r.status_code != 200 or "- Search -" in title:
        return {"doi": doi, "status": "miss", "pdf_url": None, "title": title}
    pdf_m = PDF_RE.search(r.text)
    return {
        "doi": doi,
        "status": "hit" if pdf_m else "hit_no_link",
        "pdf_url": pdf_m.group(0) if pdf_m else None,
        "title": title,
    }


def download_pdf(pdf_url: str, dest: str, session: requests.Session, timeout: int = 90) -> bool:
    """Fetch the SciDB fast-download link; write to ``dest`` only if it is a real PDF."""
    try:
        r = session.get(pdf_url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200 and r.content[:5] == b"%PDF-":
            with open(dest, "wb") as fh:
                fh.write(r.content)
            return True
    except Exception:
        pass
    return False


def read_dois(path: str) -> list[str]:
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(normalize_doi(line))
    return out


def run_dataset(path: str, args, session: requests.Session) -> dict:
    dois = read_dois(path)
    hits = 0
    for i, doi in enumerate(dois):
        res = scidb_lookup(doi, session)
        if res["status"] == "hit":
            hits += 1
            if args.download and res["pdf_url"]:
                safe = re.sub(r"[^\w.-]", "_", doi)
                ok = download_pdf(res["pdf_url"], os.path.join(args.download, safe + ".pdf"), session)
                res["downloaded"] = ok
        if not args.quiet:
            print(json.dumps(res, ensure_ascii=False))
        time.sleep(args.delay)
    total = len(dois)
    pct = (100.0 * hits / total) if total else 0.0
    return {"dataset": os.path.basename(path), "total": total, "hits": hits, "coverage_pct": round(pct, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Anna's Archive SciDB per-DOI coverage checker")
    ap.add_argument("datasets", nargs="+", help="DOI-list text file(s), one DOI per line")
    ap.add_argument("--download", metavar="DIR", help="also download hit PDFs into DIR")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between DOIs (politeness; default 1)")
    ap.add_argument("--quiet", action="store_true", help="summary table only, no per-DOI JSONL")
    args = ap.parse_args()
    if args.download:
        os.makedirs(args.download, exist_ok=True)
    session = requests.Session()
    summaries = [run_dataset(p, args, session) for p in args.datasets]
    print("\n=== coverage summary ===", file=sys.stderr)
    for s in summaries:
        print(f"  {s['dataset']:30s} {s['hits']:>5}/{s['total']:<5} = {s['coverage_pct']:>5}%", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
