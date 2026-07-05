"""Non-blocking single-key input for interactive panel."""

from __future__ import annotations

import select
import sys
import termios
import threading
import tty
from collections import deque
from typing import Callable


class KeyListener:
    def __init__(self) -> None:
        self._keys: deque[str] = deque()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not sys.stdin.isatty() or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

    def pop_key(self) -> str | None:
        with self._lock:
            if not self._keys:
                return None
            return self._keys.popleft()

    def _run(self) -> None:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not ready:
                    continue
                char = sys.stdin.read(1)
                if char:
                    with self._lock:
                        self._keys.append(char)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def drain_keys(listener: KeyListener, handler: Callable[[str], bool]) -> None:
    while True:
        key = listener.pop_key()
        if key is None:
            return
        if handler(key):
            return
