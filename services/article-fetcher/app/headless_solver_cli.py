"""Subprocess entrypoint that runs ONE stealth-browser PDF solve on its MAIN thread.

WHY a separate process: Playwright's *sync* API must run on the interpreter's main
thread (it installs signal handlers / drives its own event loop). In production both
callers of the headless tier are OFF the main thread — the real fetch runs inside a
FastAPI ``BackgroundTasks`` threadpool worker, and any startup warm-up ran in a daemon
thread. Calling ``invisible_playwright`` from a non-main thread does not error, it HANGS
indefinitely, holding the single browser semaphore forever. Running the solve in its own
process gives it a real main thread (the ~9s path that actually works) AND lets the parent
impose a hard kill-timeout via ``subprocess.run(timeout=...)``.

Contract: ``python -m app.headless_solver_cli <url> <out_path> [mode]``.
- ``mode`` is optional and defaults to ``pdf`` (the original 2-arg contract keeps
  working byte-for-byte unchanged). ``html`` solves the same JS interstitial but
  writes the RENDERED PAGE HTML instead of fetching a PDF — used by cyberleninka's
  headless full-text rescue path (``app.headless_downloader.fetch_html_via_headless``).
- exit 0  -> the solved payload (PDF bytes, or UTF-8 HTML bytes in ``html`` mode)
  was written to ``out_path`` (atomic: temp file + rename).
- exit 1  -> failure; a short message is written to stderr and NO file is left at
  ``out_path`` (so the parent never reads a partial/invalid payload).

This module is intentionally self-contained: no FastAPI/uvicorn imports, only config +
the pure solve helpers shared with ``headless_downloader`` (so the solve logic is not
duplicated). It is run by ``download_pdf_via_headless`` (the parent side).
"""
import os
import sys
import time

from app.config import settings
from app.fetcher import FetchError

# Reuse the EXISTING solve logic — do not duplicate it. ``_solve_and_fetch`` owns the
# browser/Display lifecycle and the shared wall-clock deadline budget.
from app.headless_downloader import _solve_and_fetch


def _solve(url: str, *, return_html: bool = False) -> bytes:
    """Run the shared solve on THIS process's main thread and return PDF bytes.

    Establishes the same single wall-clock deadline the in-process path used
    (``time.monotonic() + headless_fetch_timeout``) so goto + clearance-wait + PDF
    fetch together can never exceed the budget. Raises ``FetchError`` on any failure.

    ``return_html`` forwards to ``_solve_and_fetch`` (see there): when True, the
    rendered page HTML is returned (UTF-8 encoded) instead of a PDF. The keyword
    is only passed through when True, so the default ``pdf`` mode's call shape is
    byte-for-byte identical to before this option existed.
    """
    try:
        from invisible_playwright import InvisiblePlaywright
    except ImportError as exc:
        raise FetchError(
            "headless fetch unavailable: invisible_playwright not installed"
        ) from exc

    deadline = time.monotonic() + max(1, settings.headless_fetch_timeout)
    if return_html:
        return _solve_and_fetch(InvisiblePlaywright, url, deadline, return_html=True)
    return _solve_and_fetch(InvisiblePlaywright, url, deadline)


def _write_atomic(out_path: str, data: bytes) -> None:
    """Write ``data`` to ``out_path`` atomically (temp file in the same dir + rename).

    Only on success is a file ever present at ``out_path``; a crash mid-write leaves a
    ``.tmp`` sibling, never a half-written ``out_path`` the parent might read.
    """
    tmp = f"{out_path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)


def main(argv: list[str]) -> int:
    """Solve ``argv[1]`` and write the result to ``argv[2]``. Returns a process exit code.

    ``argv[3]`` (optional) selects the mode: ``pdf`` (default, unchanged contract)
    or ``html`` (writes the rendered page HTML instead of a PDF).
    """
    if len(argv) not in (3, 4):
        sys.stderr.write("usage: python -m app.headless_solver_cli <url> <out_path> [mode]\n")
        return 2

    url = argv[1]
    out_path = argv[2]
    mode = argv[3] if len(argv) == 4 else "pdf"
    try:
        result_bytes = _solve(url, return_html=(mode == "html"))
    except FetchError as exc:
        sys.stderr.write(f"headless solver failed: {exc}\n")
        return 1
    except Exception as exc:  # never crash with a traceback the parent must parse
        sys.stderr.write(f"headless solver error: {exc}\n")
        return 1

    try:
        _write_atomic(out_path, result_bytes)
    except Exception as exc:
        sys.stderr.write(f"headless solver could not write output: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
