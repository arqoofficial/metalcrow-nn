"""Stealth headless-browser PDF download tier.

Gold-OA publishers (MDPI, Hindawi, ...) serve their OA PDFs behind an Akamai Bot
Manager JS interstitial (a ``bm-verify`` challenge page). curl_cffi defeats a
TLS-fingerprint 403 but receives a 200 + HTML challenge page it cannot solve,
because it does not execute JavaScript. This module is the missing rung: it
launches a patched stealth Firefox (``invisible_playwright``), navigates to the
URL, waits for the challenge to clear (a clearance cookie like ``bm-verify`` /
``ak_bmsc`` / ``cf_clearance`` appears), then obtains the real PDF bytes —
preferring the browser context's request API (cookies already valid) and
falling back to a curl_cffi cookie-handoff if that path yields no PDF.

Design constraints (all enforced here):
- **Flag-gated + lazy import:** ``invisible_playwright`` is an OPTIONAL heavy dep,
  imported INSIDE the function. On ImportError a clear ``FetchError`` is raised so
  the caller falls through gracefully (mirrors the curl_cffi optional-dep pattern).
- **Never hang:** a hard timeout bounds the browser solve and the browser is
  ALWAYS closed in a ``finally``.
- **Concurrency cap:** a module-level ``BoundedSemaphore`` caps simultaneous
  solver SUBPROCESSES (default 1) to avoid memory blow-up — a discovery run can
  hit several MDPI papers at once.
- **Subprocess isolation:** Playwright's sync API requires the interpreter's MAIN
  thread, but the real fetch runs off-main-thread (FastAPI ``BackgroundTasks``
  threadpool) where a sync-Playwright call HANGS forever (holding the semaphore).
  So ``download_pdf_via_headless`` runs the actual solve in a child process
  (``python -m app.headless_solver_cli``) which has its own main thread. The child
  is launched in its OWN session/process group (``start_new_session=True``) so a
  hung solve is HARD-killed at the PROCESS-GROUP level (``os.killpg``) — the child
  spawns Xvfb (pyvirtualdisplay) and Firefox as grandchildren, and a kill of only
  the direct child would orphan them (reparented to init, kept running, contending
  for resources). Killing the whole group leaves no orphans.
- **SSRF guard:** the URL is validated with ``assert_public_http_url`` before any
  navigation (mirrors the curl_cffi path), and the curl_cffi cookie-handoff goes
  through ``safe_get`` which re-validates every redirect hop.
- **Best-effort:** every failure mode (timeout, still-challenged, non-PDF,
  missing dep) raises ``FetchError`` so the worker never crashes.
"""
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from app.config import settings
from app.fetcher import FetchError
from app.pdf_validate import validate_pdf
from app.url_guard import UnsafeUrlError, assert_public_http_url, safe_get

logger = logging.getLogger(__name__)

# Cookie names that signal an Akamai / Cloudflare clearance has been granted.
_CLEARANCE_COOKIE_NAMES = ("bm-verify", "ak_bmsc", "bm_sv", "bm_sz", "cf_clearance", "_abck")

# NOTE: the per-domain clearance-cookie cache was REMOVED with the subprocess
# rewrite — it lived in this process's memory, but the solve now runs in a child
# process so the cache can no longer be shared in-memory. Per-run cookie reuse can
# be reintroduced later via a file/redis-backed cache if it proves worthwhile.

# Cap simultaneous solver subprocesses (memory). Sized once at import from settings.
_BROWSER_SEMAPHORE = threading.BoundedSemaphore(max(1, settings.headless_fetch_max_concurrency))

# Directory of this package's parent (i.e. ``/app`` in the image), used as the cwd
# for the ``-m app.headless_solver_cli`` child so the ``app`` package resolves.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Max time spent in the post-navigation "settle" wait. The Akamai meta-refresh
# interstitial reloads after ~5s, so ~6s covers it; we deliberately do NOT wait
# for ``networkidle`` (publisher pages keep analytics/Akamai sockets open and
# never go idle, which used to burn the entire budget for no benefit).
SETTLE_MS = 6000


def _cookies_to_header(cookies: list[dict]) -> str:
    """Render a Playwright cookie list as a ``Cookie:`` header value."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))


def _curl_cffi_fetch_with_cookies(url: str, cookie_header: str, user_agent: str) -> bytes:
    """curl_cffi GET carrying handed-off browser cookies + UA, validated as PDF.

    Raises ImportError if curl_cffi is missing (handled by callers), FetchError on
    an unsafe URL or a non-PDF response.
    """
    from curl_cffi import requests as cffi_requests  # optional dep

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
        "Cookie": cookie_header,
    }
    try:
        resp = safe_get(
            cffi_requests.get, url, impersonate="chrome",
            timeout=settings.headless_fetch_timeout, headers=headers,
        )
    except UnsafeUrlError as exc:
        raise FetchError(f"Blocked unsafe URL {url}: {exc}") from exc
    return validate_pdf(url, resp.status_code, resp.content, resp.headers.get("content-type", ""))


def download_pdf_via_headless(url: str) -> bytes:
    """Fetch a PDF from ``url`` by solving its JS interstitial in a child process.

    PARENT side. The actual stealth-browser solve runs in a SUBPROCESS
    (``python -m app.headless_solver_cli``) because Playwright's sync API requires
    the interpreter's main thread, while the real fetch path is off-main-thread
    (FastAPI ``BackgroundTasks`` threadpool) where the call would hang forever. The
    child has its own main thread (the working ~9s path).

    The child is launched with ``start_new_session=True`` so it (and the Xvfb +
    Firefox grandchildren it spawns) form a new process group whose pgid == child
    pid. On timeout or any error exit the ENTIRE group is killed with
    ``os.killpg(...SIGKILL)`` — killing only the direct child would leave Xvfb and
    Firefox orphaned (reparented to init) and accumulating across runs, which
    manifested as a fake ">5 min cold-start stall" on later solves. A ``finally``
    defensively kills the group if anything left it running, so NO path leaks it.

    Flow: validate URL (SSRF) -> acquire concurrency slot -> spawn the solver child
    against a temp out-path -> on rc0 read+validate the bytes, else raise FetchError
    with the child's stderr. The temp file is always deleted; the slot always
    released. Always raises ``FetchError`` on any failure.
    """
    # SSRF guard before doing anything heavy (mirrors the curl_cffi path). Done in
    # the parent so an unsafe URL never even spawns a child.
    try:
        assert_public_http_url(url)
    except UnsafeUrlError as exc:
        raise FetchError(f"Blocked unsafe URL {url}: {exc}") from exc

    # The queue-wait for a solver slot has its OWN budget (separate from the
    # per-solve wall-clock budget the child enforces) so a long queue never eats
    # into the actual solve time once we hold the slot.
    acquired = _BROWSER_SEMAPHORE.acquire(timeout=settings.headless_fetch_timeout)
    if not acquired:
        raise FetchError(
            f"headless fetch for {url} timed out waiting for a browser slot "
            f"(>{settings.headless_fetch_timeout}s)"
        )
    # Allocate (but do not open) a temp path for the child to write the PDF into.
    fd, out_path = tempfile.mkstemp(suffix=".pdf", prefix="headless_")
    os.close(fd)
    # HARD kill-timeout = the child's own wall-clock budget + slack for process
    # startup/teardown. On timeout the child's ENTIRE process group (it + Xvfb +
    # Firefox) is killed, so a hung solve can NEVER hold the slot indefinitely NOR
    # orphan its browser grandchildren.
    hard_timeout = settings.headless_fetch_timeout + 15
    # ``start_new_session=True`` makes the child a session/group leader: its pgid ==
    # its pid, and every grandchild (Xvfb, Firefox) inherits that group. Killing the
    # group (not just the child) is what prevents orphan accumulation.
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.headless_solver_cli", url, out_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=_APP_ROOT,
        start_new_session=True,
    )
    try:
        try:
            _, stderr_bytes = proc.communicate(timeout=hard_timeout)
        except subprocess.TimeoutExpired as exc:
            _killpg(proc)
            # killpg misses Playwright's detached Firefox (own session); sweep it.
            _sweep_browser_processes()
            # Reap the killed child (and drain its pipes) so it is not left a zombie.
            try:
                proc.communicate()
            except Exception:
                logger.debug("headless: reap after timeout-kill failed for %s", url, exc_info=True)
            raise FetchError(
                f"headless fetch for {url} timed out (>{hard_timeout}s); process group killed"
            ) from exc

        if proc.returncode != 0:
            stderr = (stderr_bytes or b"").decode("utf-8", "replace").strip()
            raise FetchError(
                f"headless solver subprocess failed (rc={proc.returncode}) for {url}: "
                f"{stderr[:500]}"
            )

        try:
            with open(out_path, "rb") as fh:
                content = fh.read()
        except OSError as exc:
            raise FetchError(
                f"headless solver reported success but produced no output for {url}: {exc}"
            ) from exc
        # Defensive re-validation in the parent (the child already validated).
        return validate_pdf(url, 200, content, "application/pdf")
    finally:
        # Defensive: if the child is still running for ANY reason (an exception path
        # that did not reach the timeout handler), kill its whole group and reap it.
        # On a clean exit ``poll()`` is non-None and this is a no-op.
        if proc.poll() is None:
            _killpg(proc)
            # killpg misses Playwright's detached Firefox (own session); sweep it.
            _sweep_browser_processes()
            try:
                proc.communicate()
            except Exception:
                logger.debug("headless: defensive reap failed for %s", url, exc_info=True)
        _BROWSER_SEMAPHORE.release()
        try:
            os.unlink(out_path)
        except OSError:
            logger.debug("headless: could not delete temp file %s", out_path, exc_info=True)


def _killpg(proc: subprocess.Popen) -> None:
    """SIGKILL the child's whole process group (child + Xvfb + Firefox grandchildren).

    Guarded against ``ProcessLookupError``/``OSError`` so a group that has already
    exited (e.g. a clean solve whose ``pyvirtualdisplay.Display`` context already
    stopped Xvfb, leaving the group empty) is a harmless no-op.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        logger.debug("headless: process group already gone for pid %s", proc.pid, exc_info=True)


def _sweep_browser_processes() -> None:
    """Best-effort SIGKILL sweep of any detached Firefox/Xvfb left after a force-kill.

    ``_killpg`` reaps the child plus the session-sharing Xvfb in the common case, but
    Playwright ``setsid``s Firefox into its OWN session/process group, so a detached
    Firefox survives ``os.killpg(child_group)``. On the FORCE-KILL paths only (we had
    to SIGKILL a wedged child) we brute-``pkill`` firefox + Xvfb as the backstop for
    that reachable-only-by-name browser. This is safe ONLY because concurrency is
    capped at 1 (``headless_fetch_max_concurrency=1``): at any instant at most ONE
    solve's browser exists, so a name-based sweep cannot kill a sibling solve's
    browser. With ``init: true`` on the container (tini as PID 1) the killed
    processes are reaped rather than lingering as zombies. Never raises; pkill
    returns 1 when nothing matched, which we ignore.
    """
    for args in (["pkill", "-9", "-f", "firefox"], ["pkill", "-9", "-x", "Xvfb"]):
        try:
            subprocess.run(args, check=False)
        except Exception:
            logger.debug("headless: browser sweep %s failed", args, exc_info=True)


def _remaining_ms(deadline: float) -> int:
    """Milliseconds left until ``deadline`` (monotonic seconds), clamped at 0."""
    return max(0, int((deadline - time.monotonic()) * 1000))


def _solve_and_fetch(invisible_playwright_cls, url: str, deadline: float) -> bytes:
    """Inner solve: owns the browser lifecycle and ALWAYS closes it.

    ``invisible_playwright`` launches Firefox HEADED (stealth), so a real X server
    must exist or the launch aborts. The fetcher runs as a long-lived uvicorn
    service (not under ``xvfb-run``), so we start an own virtual display
    (``pyvirtualdisplay`` -> Xvfb) around the browser work. The ``Display``
    context manager ALWAYS stops the X server on exit. ``pyvirtualdisplay`` is an
    optional heavy dep imported lazily here; on ImportError a ``FetchError`` is
    raised so the caller falls through (mirrors the invisible_playwright pattern).
    """
    try:
        from pyvirtualdisplay import Display  # optional dep; needs the Xvfb binary
    except ImportError as exc:
        raise FetchError(
            "headless fetch unavailable: pyvirtualdisplay not installed"
        ) from exc

    try:
        # Virtual X display wraps the entire browser lifecycle; stopped on exit.
        with Display(visible=False, size=(1280, 1024)):
            with invisible_playwright_cls() as browser:
                context = browser.new_context()
                try:
                    page = context.new_page()
                    goto_ms = _remaining_ms(deadline)
                    if goto_ms <= 0:
                        raise FetchError(
                            f"headless fetch for {url} exhausted its budget before navigation"
                        )
                    page.goto(url, timeout=goto_ms, wait_until="domcontentloaded")

                    # Wait for the JS challenge to clear: a short bounded settle
                    # plus a clearance-cookie inspection. Best-effort + bounded by
                    # the SHARED deadline (never waits for networkidle).
                    _wait_for_clearance(page, context, url, deadline)

                    cookies = context.cookies()
                    cookie_header = _cookies_to_header(cookies)
                    user_agent = _current_user_agent(page)

                    fetch_ms = _remaining_ms(deadline)
                    if fetch_ms <= 0:
                        raise FetchError(
                            f"headless fetch for {url} exhausted its budget before the PDF fetch"
                        )
                    content = _fetch_pdf_in_context(context, url, fetch_ms)
                    if content is None:
                        # Cookie-handoff fallback: re-fetch with curl_cffi carrying
                        # the browser's clearance cookies + UA.
                        content = _cookie_handoff_fetch(url, cookie_header, user_agent)
                    return content
                finally:
                    try:
                        context.close()
                    except Exception:
                        logger.debug("headless: context.close() failed for %s", url, exc_info=True)
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"headless fetch failed for {url}: {exc}") from exc


def _wait_for_clearance(page, context, url: str, deadline: float) -> None:
    """Best-effort wait for the interstitial to clear; never raises.

    Does a SHORT fixed settle (``SETTLE_MS``, bounded by the shared deadline)
    instead of ``wait_for_load_state("networkidle")`` — publisher pages keep
    persistent analytics/Akamai sockets open and rarely reach networkidle, so the
    old wait burned the full budget for no benefit. ~6s covers the Akamai
    meta-refresh interstitial; the subsequent PDF validation is the real gate.
    """
    settle_ms = min(_remaining_ms(deadline), SETTLE_MS)
    if settle_ms > 0:
        try:
            page.wait_for_timeout(settle_ms)
        except Exception:
            logger.debug("headless: settle wait failed for %s", url, exc_info=True)
    # A clearance cookie appearing is a strong signal the challenge passed; we do
    # not hard-fail if it is absent (some publishers gate without a named cookie),
    # the subsequent PDF validation is the real gate.
    try:
        cookie_names = {c.get("name") for c in context.cookies()}
        if cookie_names & set(_CLEARANCE_COOKIE_NAMES):
            logger.debug("headless: clearance cookie present for %s", url)
    except Exception:
        logger.debug("headless: cookie inspection failed for %s", url, exc_info=True)


def _current_user_agent(page) -> str:
    try:
        ua = page.evaluate("() => navigator.userAgent")
        if isinstance(ua, str) and ua:
            return ua
    except Exception:
        logger.debug("headless: could not read navigator.userAgent", exc_info=True)
    return ""


def _fetch_pdf_in_context(context, url: str, timeout_ms: int) -> Optional[bytes]:
    """Fetch the PDF through the browser context's request API (cookies valid).

    Returns validated bytes, or None if the request yields a non-PDF (so the
    caller can try the cookie-handoff). Never raises.
    """
    try:
        request_api = context.request
        resp = request_api.get(url, timeout=timeout_ms)
        status = resp.status
        body = resp.body()
        content_type = resp.headers.get("content-type", "") if hasattr(resp, "headers") else ""
        return validate_pdf(url, status, body, content_type)
    except FetchError:
        return None
    except Exception:
        logger.debug("headless: in-context request for %s failed", url, exc_info=True)
        return None


def _cookie_handoff_fetch(url: str, cookie_header: str, user_agent: str) -> bytes:
    """curl_cffi cookie-handoff fallback when the in-browser request yields no PDF."""
    if not cookie_header:
        raise FetchError(f"headless solve for {url} produced no clearance cookies to hand off")
    try:
        return _curl_cffi_fetch_with_cookies(url, cookie_header, user_agent)
    except ImportError as exc:
        raise FetchError(
            f"headless cookie-handoff for {url} needs curl_cffi, which is not installed"
        ) from exc
