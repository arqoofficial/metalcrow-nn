"""Tests for the headless stealth-browser PDF fetch tier (subprocess architecture).

The real solve runs in a child process (``python -m app.headless_solver_cli``)
because Playwright's sync API needs the main thread. Unit tests NEVER launch a real
browser or a real subprocess — ``subprocess.Popen`` is mocked on the PARENT side, and
the solve helpers (``_solve_and_fetch`` / Display) are mocked on the CLI side. We
assert:
- parent: rc0 + a written ``%PDF`` file -> returns bytes; rc1 + stderr -> FetchError;
  ``TimeoutExpired`` -> FetchError + the whole process group is SIGKILLed (no orphan
  Xvfb/Firefox); the child runs in its own session (``start_new_session=True``); the
  semaphore is acquired and released on every path; the temp file is cleaned up; a
  finally-time killpg fires if the child is still running; SSRF + flag/dep-missing
  paths fall through;
- CLI: writes validated bytes to out_path on success (exit 0), exits non-zero on
  ``_solve`` failure and leaves no output file behind;
- the shared solve helpers (``_solve_and_fetch`` / ``_wait_for_clearance`` /
  budget logic) still behave with a fake browser + fake Display.
"""
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from app.fetcher import FetchError


# --------------------------------------------------------------------------- #
# Helpers to fake a requests / curl_cffi response
# --------------------------------------------------------------------------- #
def _resp(content=b"%PDF-1.4 ok", status=200, content_type="application/pdf"):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.headers = {"content-type": content_type}
    return r


def _fail_plain_and_cffi(monkeypatch):
    """Make both the plain GET and curl_cffi paths raise FetchError (no network)."""
    from app import main

    def _boom_plain(*a, **k):
        # safe_get is the first call in the plain path; raising here avoids real DNS.
        raise FetchError("plain GET blocked")

    def _boom_cffi(url):
        raise FetchError("curl_cffi blocked")

    monkeypatch.setattr(main, "safe_get", _boom_plain)
    monkeypatch.setattr(main, "_download_pdf_via_curl_cffi", _boom_cffi)


def _make_fake_popen(
    *, returncode=0, stderr=b"", timeout=False, on_start=None, stay_alive=False
):
    """Build a fake ``subprocess.Popen`` factory for the PARENT side.

    The factory (call it like ``Popen(cmd, **kwargs)``) records ``cmd``/``kwargs`` and
    returns a fake Popen instance whose:
    - ``communicate(timeout=...)``: when ``timeout`` is set, the FIRST call raises
      ``subprocess.TimeoutExpired`` (the kill path) and the SECOND (the reap) returns
      ``(b"", stderr)``; otherwise it returns ``(b"", stderr)`` and marks done.
    - ``poll()``: ``None`` while "running", ``returncode`` once a non-timeout
      ``communicate`` completed. With ``stay_alive=True`` it reports ``None`` once
      MORE after completion so the finally-time defensive killpg branch fires.
    - ``pid``: a fixed sentinel.
    ``on_start(cmd)`` runs at construction so a test can write the child's out file.
    """
    import subprocess as _sp

    should_timeout = timeout  # capture; param 'timeout' below is communicate's kwarg
    fake = MagicMock()
    fake.pid = 4242
    fake.returncode = None
    state = {"done": False, "calls": 0, "extra_alive": stay_alive}

    def _communicate(timeout=None):
        state["calls"] += 1
        # The kill path: the FIRST communicate (the one given a wall-clock timeout)
        # times out; the reap call (timeout=None) returns cleanly.
        if should_timeout and timeout is not None and state["calls"] == 1:
            raise _sp.TimeoutExpired(cmd="child", timeout=timeout)
        state["done"] = True
        fake.returncode = returncode
        return (b"", stderr)

    fake.communicate.side_effect = _communicate

    def _poll():
        if not state["done"]:
            return None
        if state["extra_alive"]:
            state["extra_alive"] = False
            return None  # finally-branch sees it "still running" -> killpg fires
        return fake.returncode

    fake.poll.side_effect = _poll

    def _factory(cmd, **kwargs):
        _factory.cmd = cmd
        _factory.kwargs = kwargs
        if on_start is not None:
            on_start(cmd)
        return fake

    _factory.fake = fake
    return _factory


# --------------------------------------------------------------------------- #
# Flag OFF -> headless never reached, original FetchError preserved
# --------------------------------------------------------------------------- #
def test_flag_off_does_not_call_headless(monkeypatch):
    from app import main

    _fail_plain_and_cffi(monkeypatch)
    monkeypatch.setattr(main.settings, "headless_fetch_enabled", False)

    called = {"n": 0}

    def _spy(url):
        called["n"] += 1
        return b"%PDF-headless"

    # Even if a headless module existed, the flag-off path must not import/call it.
    with patch.dict(
        sys.modules,
        {"app.headless_downloader": types.SimpleNamespace(download_pdf_via_headless=_spy)},
    ):
        with pytest.raises(FetchError):
            main._download_pdf_from_url("https://mdpi.com/paper.pdf")

    assert called["n"] == 0


# --------------------------------------------------------------------------- #
# Flag ON + curl_cffi fails -> headless attempted, success returns bytes
# --------------------------------------------------------------------------- #
def test_flag_on_headless_success(monkeypatch):
    from app import main

    _fail_plain_and_cffi(monkeypatch)
    monkeypatch.setattr(main.settings, "headless_fetch_enabled", True)

    fake_mod = types.SimpleNamespace(
        download_pdf_via_headless=lambda url: b"%PDF-headless bytes"
    )
    with patch.dict(sys.modules, {"app.headless_downloader": fake_mod}):
        out = main._download_pdf_from_url("https://mdpi.com/paper.pdf")

    assert out == b"%PDF-headless bytes"


def test_flag_on_headless_non_pdf_raises(monkeypatch):
    from app import main

    _fail_plain_and_cffi(monkeypatch)
    monkeypatch.setattr(main.settings, "headless_fetch_enabled", True)

    def _non_pdf(url):
        raise FetchError("headless got HTML challenge, not a PDF")

    fake_mod = types.SimpleNamespace(download_pdf_via_headless=_non_pdf)
    with patch.dict(sys.modules, {"app.headless_downloader": fake_mod}):
        with pytest.raises(FetchError) as ei:
            main._download_pdf_from_url("https://mdpi.com/paper.pdf")

    assert "headless" in str(ei.value)


# --------------------------------------------------------------------------- #
# download_pdf_via_headless PARENT side: subprocess.Popen is mocked.
# --------------------------------------------------------------------------- #
def test_parent_subprocess_success_returns_bytes(monkeypatch, tmp_path):
    """rc0 + the child wrote a %PDF file -> parent reads and returns the bytes,
    semaphore acquired+released, child launched in its own session, temp cleaned up."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)

    events = []
    real_acquire = hd._BROWSER_SEMAPHORE.acquire
    real_release = hd._BROWSER_SEMAPHORE.release
    monkeypatch.setattr(hd._BROWSER_SEMAPHORE, "acquire",
                        lambda *a, **k: (events.append("acquire"), real_acquire(*a, **k))[1])
    monkeypatch.setattr(hd._BROWSER_SEMAPHORE, "release",
                        lambda *a, **k: (events.append("release"), real_release(*a, **k))[1])

    written = {}

    def _on_start(cmd):
        events.append("run")
        # cmd = [python, -m, app.headless_solver_cli, url, out_path]
        out_path = cmd[-1]
        written["out_path"] = out_path
        with open(out_path, "wb") as fh:
            fh.write(b"%PDF-1.4 child output")

    factory = _make_fake_popen(returncode=0, on_start=_on_start)
    monkeypatch.setattr(hd.subprocess, "Popen", factory)

    # The browser sweep (pkill) must NOT run on the clean-success path.
    sweep_calls = []
    monkeypatch.setattr(hd.subprocess, "run",
                        lambda args, **k: sweep_calls.append(args))

    out = hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")
    assert out == b"%PDF-1.4 child output"
    assert events == ["acquire", "run", "release"]
    # No force-kill happened, so no pkill sweep on success.
    assert sweep_calls == []
    # Child must be launched in its own session/process group (orphan prevention).
    assert factory.kwargs.get("start_new_session") is True
    assert factory.kwargs.get("cwd") == hd._APP_ROOT
    # Temp file deleted in finally.
    import os
    assert not os.path.exists(written["out_path"])


def test_parent_subprocess_nonzero_raises_with_stderr(monkeypatch):
    """rc1 + stderr -> FetchError carrying the trimmed stderr; slot released; no leak."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)

    captured = {}

    def _on_start(cmd):
        captured["out_path"] = cmd[-1]

    factory = _make_fake_popen(
        returncode=1, stderr=b"headless solver failed: HTML challenge", on_start=_on_start
    )
    monkeypatch.setattr(hd.subprocess, "Popen", factory)

    with pytest.raises(FetchError) as ei:
        hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")
    assert "HTML challenge" in str(ei.value)
    # Slot must have been released (releasing once more raises ValueError -> proof it
    # was at full count, i.e. released).
    with pytest.raises(ValueError):
        hd._BROWSER_SEMAPHORE.release()
    import os
    assert not os.path.exists(captured["out_path"])


def test_parent_subprocess_timeout_kills_process_group(monkeypatch):
    """communicate(timeout) -> TimeoutExpired -> the WHOLE process group is SIGKILLed
    (no orphaned Xvfb/Firefox), FetchError raised, slot released."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)

    factory = _make_fake_popen(timeout=True)
    monkeypatch.setattr(hd.subprocess, "Popen", factory)

    # Order tracking: killpg must happen, then the browser sweep (pkill) as backstop.
    order = []
    monkeypatch.setattr(hd.os, "getpgid", lambda pid: pid)  # pgid == pid (session leader)

    killed = {}

    def _killpg_spy(pgid, sig):
        order.append("killpg")
        killed.update(pgid=pgid, sig=sig)

    monkeypatch.setattr(hd.os, "killpg", _killpg_spy)

    # Patch subprocess.run (the pkill backstop) so no real pkill runs.
    pkill_calls = []

    def _fake_run(args, **kwargs):
        order.append("pkill")
        pkill_calls.append(args)
        return MagicMock(returncode=1)  # pkill returns 1 when nothing matched

    monkeypatch.setattr(hd.subprocess, "run", _fake_run)

    with pytest.raises(FetchError) as ei:
        hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")
    msg = str(ei.value).lower()
    assert "timed out" in msg
    assert "process group killed" in msg
    # The fake child's pid is 4242; killpg must have targeted its group with SIGKILL.
    assert killed["pgid"] == 4242
    assert killed["sig"] == hd.signal.SIGKILL
    # The browser sweep runs AFTER killpg (backstop for detached Firefox).
    assert order.index("killpg") < order.index("pkill")
    # Both firefox + Xvfb sweeps fired.
    assert ["pkill", "-9", "-f", "firefox"] in pkill_calls
    assert ["pkill", "-9", "-x", "Xvfb"] in pkill_calls
    # Killed child is reaped (communicate called twice: the timed-out wait + the reap).
    assert factory.fake.communicate.call_count == 2
    with pytest.raises(ValueError):
        hd._BROWSER_SEMAPHORE.release()


def test_parent_finally_kills_lingering_group(monkeypatch):
    """If the child is somehow still running at the finally (poll() is None), the
    defensive branch SIGKILLs its group so no path can leak an orphaned browser."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)

    def _on_start(cmd):
        with open(cmd[-1], "wb") as fh:
            fh.write(b"%PDF ok")

    # stay_alive=True: poll() reports None once after completion -> finally killpg fires.
    factory = _make_fake_popen(returncode=0, on_start=_on_start, stay_alive=True)
    monkeypatch.setattr(hd.subprocess, "Popen", factory)

    killed = {}
    monkeypatch.setattr(hd.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(hd.os, "killpg",
                        lambda pgid, sig: killed.update(pgid=pgid, sig=sig))

    # The finally-time force-kill must also fire the browser sweep.
    sweep_calls = []
    monkeypatch.setattr(hd.subprocess, "run",
                        lambda args, **k: sweep_calls.append(args))

    out = hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")
    assert out == b"%PDF ok"
    assert killed.get("pgid") == 4242
    assert killed.get("sig") == hd.signal.SIGKILL
    # Sweep ran on the finally force-kill branch (backstop for detached Firefox).
    assert ["pkill", "-9", "-f", "firefox"] in sweep_calls
    assert ["pkill", "-9", "-x", "Xvfb"] in sweep_calls


def test_killpg_swallows_process_lookup(monkeypatch):
    """_killpg must be a no-op when the group has already exited (clean solve case)."""
    from app import headless_downloader as hd

    proc = MagicMock()
    proc.pid = 99
    monkeypatch.setattr(hd.os, "getpgid", lambda pid: pid)

    def _boom(pgid, sig):
        raise ProcessLookupError("group already gone")

    monkeypatch.setattr(hd.os, "killpg", _boom)
    # Must not raise.
    hd._killpg(proc)


def test_sweep_browser_processes_runs_pkill(monkeypatch):
    """_sweep_browser_processes brute-pkills firefox + Xvfb (concurrency==1 makes it safe)."""
    from app import headless_downloader as hd

    calls = []
    monkeypatch.setattr(hd.subprocess, "run",
                        lambda args, **k: calls.append(args) or MagicMock(returncode=1))

    hd._sweep_browser_processes()
    assert ["pkill", "-9", "-f", "firefox"] in calls
    assert ["pkill", "-9", "-x", "Xvfb"] in calls


def test_sweep_browser_processes_never_raises(monkeypatch):
    """A missing/erroring pkill (FileNotFoundError, nonzero rc) must NOT propagate."""
    from app import headless_downloader as hd

    def _boom(args, **k):
        raise FileNotFoundError("pkill not installed")

    monkeypatch.setattr(hd.subprocess, "run", _boom)
    # Must swallow the error — best-effort backstop.
    hd._sweep_browser_processes()


def test_timeout_sweep_failure_still_raises_fetcherror(monkeypatch):
    """If pkill is missing on the timeout path, the parent still raises FetchError
    (the sweep is best-effort and must never crash the kill path)."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)

    factory = _make_fake_popen(timeout=True)
    monkeypatch.setattr(hd.subprocess, "Popen", factory)
    monkeypatch.setattr(hd.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(hd.os, "killpg", lambda pgid, sig: None)

    def _boom(args, **k):
        raise FileNotFoundError("pkill not installed")

    monkeypatch.setattr(hd.subprocess, "run", _boom)

    with pytest.raises(FetchError) as ei:
        hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")
    assert "timed out" in str(ei.value).lower()
    with pytest.raises(ValueError):
        hd._BROWSER_SEMAPHORE.release()


def test_parent_hard_timeout_value(monkeypatch):
    """The communicate hard timeout = headless_fetch_timeout + 15 (kills a hung browser)."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)
    monkeypatch.setattr(hd.settings, "headless_fetch_timeout", 60)

    captured = {}

    def _on_start(cmd):
        captured["cwd_will_be_checked"] = True
        with open(cmd[-1], "wb") as fh:
            fh.write(b"%PDF ok")

    factory = _make_fake_popen(returncode=0, on_start=_on_start)
    # Record the timeout the parent passes to communicate.
    orig_comm = factory.fake.communicate.side_effect

    def _record_comm(timeout=None):
        if "timeout" not in captured:
            captured["timeout"] = timeout
        return orig_comm(timeout)

    factory.fake.communicate.side_effect = _record_comm
    monkeypatch.setattr(hd.subprocess, "Popen", factory)

    hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")
    assert captured["timeout"] == 75  # 60 + 15
    assert factory.kwargs.get("cwd") == hd._APP_ROOT


def test_parent_rc0_but_no_file_raises(monkeypatch):
    """rc0 but the child produced no readable output -> FetchError, not a crash."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)

    def _on_start(cmd):
        # Delete the pre-created temp file to simulate a missing output.
        import os
        try:
            os.unlink(cmd[-1])
        except OSError:
            pass

    factory = _make_fake_popen(returncode=0, on_start=_on_start)
    monkeypatch.setattr(hd.subprocess, "Popen", factory)
    with pytest.raises(FetchError):
        hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")


def test_parent_invokes_solver_cli_module(monkeypatch):
    """The child command targets `-m app.headless_solver_cli <url> <out_path>`."""
    from app import headless_downloader as hd

    monkeypatch.setattr(hd, "assert_public_http_url", lambda url: None)

    def _on_start(cmd):
        with open(cmd[-1], "wb") as fh:
            fh.write(b"%PDF ok")

    factory = _make_fake_popen(returncode=0, on_start=_on_start)
    monkeypatch.setattr(hd.subprocess, "Popen", factory)
    hd.download_pdf_via_headless("https://www.mdpi.com/a.pdf")
    cmd = factory.cmd
    assert cmd[0] == sys.executable
    assert cmd[1] == "-m"
    assert cmd[2] == "app.headless_solver_cli"
    assert cmd[3] == "https://www.mdpi.com/a.pdf"


def test_download_pdf_from_url_headless_failure_falls_through(monkeypatch):
    """When headless raises FetchError, the caller still raises FetchError
    (so _run_fetch falls to EuropePMC/Sci-Hub) rather than crashing."""
    from app import main

    _fail_plain_and_cffi(monkeypatch)
    monkeypatch.setattr(main.settings, "headless_fetch_enabled", True)

    def _unavailable(url):
        raise FetchError("headless fetch unavailable: invisible_playwright not installed")

    fake_mod = types.SimpleNamespace(download_pdf_via_headless=_unavailable)
    with patch.dict(sys.modules, {"app.headless_downloader": fake_mod}):
        with pytest.raises(FetchError):
            main._download_pdf_from_url("https://mdpi.com/paper.pdf")


# --------------------------------------------------------------------------- #
# CLI module (app.headless_solver_cli) — _solve_and_fetch is mocked, no browser.
# --------------------------------------------------------------------------- #
def test_cli_writes_bytes_on_success(monkeypatch, tmp_path):
    """main() solves and writes validated bytes to out_path, returns 0."""
    from app import headless_solver_cli as cli

    # Provide a fake invisible_playwright so the lazy import inside _solve succeeds.
    fake_ip = types.ModuleType("invisible_playwright")
    fake_ip.InvisiblePlaywright = MagicMock()
    monkeypatch.setitem(sys.modules, "invisible_playwright", fake_ip)

    monkeypatch.setattr(cli, "_solve_and_fetch", lambda cls, url, deadline: b"%PDF-1.4 solved")

    out_path = tmp_path / "out.pdf"
    rc = cli.main(["prog", "https://www.mdpi.com/a.pdf", str(out_path)])
    assert rc == 0
    assert out_path.read_bytes() == b"%PDF-1.4 solved"


def test_cli_exits_nonzero_on_failure(monkeypatch, tmp_path, capsys):
    """A FetchError from the solve -> exit 1 + stderr msg + NO output file left."""
    from app import headless_solver_cli as cli

    fake_ip = types.ModuleType("invisible_playwright")
    fake_ip.InvisiblePlaywright = MagicMock()
    monkeypatch.setitem(sys.modules, "invisible_playwright", fake_ip)

    def _boom(cls, url, deadline):
        raise FetchError("HTML challenge, not a PDF")

    monkeypatch.setattr(cli, "_solve_and_fetch", _boom)

    out_path = tmp_path / "out.pdf"
    rc = cli.main(["prog", "https://www.mdpi.com/a.pdf", str(out_path)])
    assert rc == 1
    assert not out_path.exists()
    err = capsys.readouterr().err
    assert "HTML challenge" in err


def test_cli_importerror_exits_nonzero(monkeypatch, tmp_path, capsys):
    """Missing invisible_playwright -> exit 1, no output file."""
    from app import headless_solver_cli as cli

    monkeypatch.setitem(sys.modules, "invisible_playwright", None)
    out_path = tmp_path / "out.pdf"
    rc = cli.main(["prog", "https://www.mdpi.com/a.pdf", str(out_path)])
    assert rc == 1
    assert not out_path.exists()
    assert "not installed" in capsys.readouterr().err


def test_cli_bad_argv_returns_usage_code(tmp_path):
    from app import headless_solver_cli as cli

    assert cli.main(["prog", "only-one-arg"]) == 2


# --------------------------------------------------------------------------- #
# Concurrency semaphore
# --------------------------------------------------------------------------- #
def test_concurrency_semaphore_exists():
    from app import headless_downloader as hd

    # BoundedSemaphore is a factory returning a _BoundedSemaphore instance; assert
    # the public acquire/release contract rather than an exact class identity.
    assert hasattr(hd._BROWSER_SEMAPHORE, "acquire")
    assert hasattr(hd._BROWSER_SEMAPHORE, "release")
    assert hd._BROWSER_SEMAPHORE.acquire(timeout=1)
    hd._BROWSER_SEMAPHORE.release()
    # Bounded: releasing once more than acquired raises ValueError.
    with pytest.raises(ValueError):
        hd._BROWSER_SEMAPHORE.release()


# --------------------------------------------------------------------------- #
# Virtual display wraps the browser launch (shared solve helper)
# --------------------------------------------------------------------------- #
def _install_fake_pyvirtualdisplay(monkeypatch, events):
    """Install a fake `pyvirtualdisplay` module whose Display records enter/exit."""
    class _FakeDisplay:
        def __init__(self, *a, **k):
            events.append(("display_init", a, k))

        def __enter__(self):
            events.append("display_start")
            return self

        def __exit__(self, *exc):
            events.append("display_stop")
            return False

    fake_pvd = types.ModuleType("pyvirtualdisplay")
    fake_pvd.Display = _FakeDisplay
    monkeypatch.setitem(sys.modules, "pyvirtualdisplay", fake_pvd)


def _fake_browser_cls(events, timeouts=None):
    """An InvisiblePlaywright-shaped class whose context yields a valid PDF.

    If ``timeouts`` (a dict) is passed, the timeout each Playwright step receives
    is recorded under keys ``goto``/``settle``/``request`` so a test can assert the
    shared-deadline budget logic.
    """
    if timeouts is None:
        timeouts = {}

    resp = MagicMock()
    resp.status = 200
    resp.body.return_value = b"%PDF-1.4 real"
    resp.headers = {"content-type": "application/pdf"}

    page = MagicMock()

    def _goto(url, timeout=None, wait_until=None):
        timeouts["goto"] = timeout

    page.goto.side_effect = _goto

    def _wait_for_timeout(ms):
        timeouts["settle"] = ms

    page.wait_for_timeout.side_effect = _wait_for_timeout

    def _wait_for_load_state(*a, **k):
        timeouts["networkidle_called"] = True

    page.wait_for_load_state.side_effect = _wait_for_load_state
    page.evaluate.return_value = "UA/test"

    context = MagicMock()
    context.new_page.return_value = page

    def _req_get(url, timeout=None):
        timeouts["request"] = timeout
        return resp

    context.request.get.side_effect = _req_get
    context.cookies.return_value = [{"name": "ak_bmsc", "value": "tok"}]

    browser = MagicMock()
    browser.new_context.return_value = context

    class _FakeIP:
        def __enter__(self_inner):
            events.append("browser_start")
            return browser

        def __exit__(self_inner, *exc):
            events.append("browser_stop")
            return False

    return _FakeIP


def test_solve_and_fetch_wraps_browser_in_display(monkeypatch):
    """_solve_and_fetch must start the virtual Display BEFORE launching the browser
    and stop it AFTER — and return the validated PDF bytes."""
    from app import headless_downloader as hd

    events = []
    _install_fake_pyvirtualdisplay(monkeypatch, events)
    fake_ip_cls = _fake_browser_cls(events)

    deadline = time.monotonic() + 30  # plenty of budget left
    out = hd._solve_and_fetch(fake_ip_cls, "https://www.mdpi.com/a.pdf", deadline)

    assert out == b"%PDF-1.4 real"
    # Display starts before the browser and stops after it (proper nesting).
    assert events.index("display_start") < events.index("browser_start")
    assert events.index("browser_stop") < events.index("display_stop")


def test_solve_and_fetch_display_importerror_is_graceful(monkeypatch):
    """Missing pyvirtualdisplay -> FetchError (caller falls through), never crash."""
    from app import headless_downloader as hd

    monkeypatch.setitem(sys.modules, "pyvirtualdisplay", None)
    with pytest.raises(FetchError) as ei:
        hd._solve_and_fetch(MagicMock(), "https://www.mdpi.com/a.pdf", time.monotonic() + 30)
    assert "pyvirtualdisplay not installed" in str(ei.value)


# --------------------------------------------------------------------------- #
# Shared wall-clock deadline / remaining-budget logic
# --------------------------------------------------------------------------- #
def test_remaining_ms_decreases_and_clamps(monkeypatch):
    from app import headless_downloader as hd

    deadline = time.monotonic() + 10
    assert hd._remaining_ms(deadline) > 0
    assert hd._remaining_ms(deadline) <= 10_000
    # Past the deadline -> clamped to 0, never negative.
    assert hd._remaining_ms(time.monotonic() - 5) == 0


def test_each_playwright_step_gets_remaining_budget(monkeypatch):
    """goto, settle and request each receive a positive, deadline-bounded timeout;
    none receives the old fixed full-timeout-per-step, and networkidle is gone."""
    from app import headless_downloader as hd

    events = []
    timeouts = {}
    _install_fake_pyvirtualdisplay(monkeypatch, events)
    fake_ip_cls = _fake_browser_cls(events, timeouts)

    deadline = time.monotonic() + 30  # 30s total budget
    out = hd._solve_and_fetch(fake_ip_cls, "https://www.mdpi.com/a.pdf", deadline)

    assert out == b"%PDF-1.4 real"
    # Each step got a remaining-budget timeout (positive, never exceeding total).
    assert 0 < timeouts["goto"] <= 30_000
    # Settle is capped at SETTLE_MS regardless of how much budget remains.
    assert timeouts["settle"] == hd.SETTLE_MS
    assert 0 < timeouts["request"] <= 30_000
    # The request runs after the settle, so its remaining budget is <= goto's.
    assert timeouts["request"] <= timeouts["goto"]
    # networkidle must NOT be used any more.
    assert "networkidle_called" not in timeouts


def test_settle_bounded_by_small_remaining_budget(monkeypatch):
    """When little budget is left, the settle shrinks below SETTLE_MS (no overrun)."""
    from app import headless_downloader as hd

    events = []
    timeouts = {}
    _install_fake_pyvirtualdisplay(monkeypatch, events)
    fake_ip_cls = _fake_browser_cls(events, timeouts)

    # Only ~2s of budget left -> settle clamped to remaining, below SETTLE_MS (6s).
    deadline = time.monotonic() + 2
    hd._solve_and_fetch(fake_ip_cls, "https://www.mdpi.com/a.pdf", deadline)
    assert timeouts["settle"] <= 2_000
    assert timeouts["settle"] < hd.SETTLE_MS


def test_networkidle_not_called_in_wait_for_clearance(monkeypatch):
    """_wait_for_clearance must do a bounded settle, never wait_for_load_state."""
    from app import headless_downloader as hd

    page = MagicMock()
    context = MagicMock()
    context.cookies.return_value = [{"name": "ak_bmsc"}]

    hd._wait_for_clearance(page, context, "https://www.mdpi.com/a.pdf", time.monotonic() + 30)

    page.wait_for_load_state.assert_not_called()
    page.wait_for_timeout.assert_called_once()
    # The single settle call is capped at SETTLE_MS.
    (settle_ms,), _ = page.wait_for_timeout.call_args
    assert settle_ms == hd.SETTLE_MS


def test_budget_exhausted_before_navigation_raises(monkeypatch):
    """A solve whose budget is already spent raises FetchError before goto."""
    from app import headless_downloader as hd

    events = []
    timeouts = {}
    _install_fake_pyvirtualdisplay(monkeypatch, events)
    fake_ip_cls = _fake_browser_cls(events, timeouts)

    deadline = time.monotonic() - 1  # already past the deadline
    with pytest.raises(FetchError) as ei:
        hd._solve_and_fetch(fake_ip_cls, "https://www.mdpi.com/a.pdf", deadline)
    assert "budget" in str(ei.value).lower()
    # goto must never have been attempted.
    assert "goto" not in timeouts


# --------------------------------------------------------------------------- #
# Warm-up is GONE
# --------------------------------------------------------------------------- #
def test_warm_browser_removed():
    """The in-process warm-up must be fully removed (subprocess arch can't use it)."""
    from app import headless_downloader as hd

    assert not hasattr(hd, "warm_browser")
    assert not hasattr(hd, "_warm_browser_blocking")


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #
def test_ssrf_guard_blocks_before_subprocess(monkeypatch):
    from app import headless_downloader as hd
    from app.url_guard import UnsafeUrlError

    def _unsafe(url):
        raise UnsafeUrlError("private IP")

    ran = {"n": 0}
    monkeypatch.setattr(hd.subprocess, "Popen",
                        lambda *a, **k: ran.__setitem__("n", ran["n"] + 1))
    monkeypatch.setattr(hd, "assert_public_http_url", _unsafe)
    with pytest.raises(FetchError) as ei:
        hd.download_pdf_via_headless("http://169.254.169.254/latest")
    assert "unsafe" in str(ei.value).lower()
    # SSRF guard must short-circuit BEFORE spawning any child.
    assert ran["n"] == 0
