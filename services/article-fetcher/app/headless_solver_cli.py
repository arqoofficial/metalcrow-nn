"""Subprocess entrypoint that runs ONE stealth-browser PDF solve on its MAIN thread.

WHY a separate process: Playwright's *sync* API must run on the interpreter's main
thread (it installs signal handlers / drives its own event loop). In production both
callers of the headless tier are OFF the main thread — the real fetch runs inside a
FastAPI ``BackgroundTasks`` threadpool worker, and any startup warm-up ran in a daemon
thread. Calling ``invisible_playwright`` from a non-main thread does not error, it HANGS
indefinitely, holding the single browser semaphore forever. Running the solve in its own
process gives it a real main thread (the ~9s path that actually works) AND lets the parent
impose a hard kill-timeout via ``subprocess.run(timeout=...)``.

Contract: ``python -m app.headless_solver_cli <url> <out_path>``.
- exit 0  -> a validated PDF was written to ``out_path`` (atomic: temp file + rename).
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


def _solve(url: str) -> bytes:
    """Run the shared solve on THIS process's main thread and return PDF bytes.

    Establishes the same single wall-clock deadline the in-process path used
    (``time.monotonic() + headless_fetch_timeout``) so goto + clearance-wait + PDF
    fetch together can never exceed the budget. Raises ``FetchError`` on any failure.
    """
    try:
        from invisible_playwright import InvisiblePlaywright
    except ImportError as exc:
        raise FetchError(
            "headless fetch unavailable: invisible_playwright not installed"
        ) from exc

    deadline = time.monotonic() + max(1, settings.headless_fetch_timeout)
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
    """Solve ``argv[1]`` and write the PDF to ``argv[2]``. Returns a process exit code."""
    if len(argv) != 3:
        sys.stderr.write("usage: python -m app.headless_solver_cli <url> <out_path>\n")
        return 2

    url = argv[1]
    out_path = argv[2]
    try:
        pdf_bytes = _solve(url)
    except FetchError as exc:
        sys.stderr.write(f"headless solver failed: {exc}\n")
        return 1
    except Exception as exc:  # never crash with a traceback the parent must parse
        sys.stderr.write(f"headless solver error: {exc}\n")
        return 1

    try:
        _write_atomic(out_path, pdf_bytes)
    except Exception as exc:
        sys.stderr.write(f"headless solver could not write output: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
