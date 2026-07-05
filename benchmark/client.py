"""HTTP-клиент контура чата + пробы здоровья компонентов.

Основная цель бенчмарка — сквозной эндпоинт чата бэкенда
(`POST /api/v1/chat/sessions/{id}/messages`, SSE). Клиент логинится
(OAuth2 password), заводит сессию и шлёт вопросы, разбирая единственный
SSE-event `data: {ChatMessageResponse}` в словарь.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import BenchConfig


class ChatError(RuntimeError):
    pass


@dataclass
class ChatTurn:
    """Результат одного обращения к чату."""

    ok: bool
    latency_s: float
    payload: dict[str, Any]  # разобранный ChatMessageResponse (или {} при ошибке)
    error: str | None = None


def _parse_sse(text: str) -> dict[str, Any]:
    """Взять последний `data:`-JSON из SSE-потока."""
    last: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            chunk = line[len("data:") :].strip()
            if chunk:
                try:
                    last = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
    if not last:
        raise ChatError("SSE без разбираемого data-JSON")
    return last


class ChatClient:
    """Логин + сессии + отправка сообщений в чат бэкенда."""

    def __init__(self, cfg: BenchConfig) -> None:
        self.cfg = cfg
        # trust_env=False: не подхватывать системный HTTP(S)_PROXY (контур локальный)
        self._http = httpx.Client(timeout=cfg.timeout_s, trust_env=False)
        self._token: str | None = None
        self._session_id: str | None = None

    # ── аутентификация ──────────────────────────────────────────────
    def login(self) -> None:
        r = self._http.post(
            f"{self.cfg.api}/login/access-token",
            data={"username": self.cfg.username, "password": self.cfg.password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            raise ChatError(
                f"Логин не удался ({r.status_code}): {r.text[:200]}. "
                f"Проверьте BENCH_USERNAME/BENCH_PASSWORD или FIRST_SUPERUSER* в .env."
            )
        self._token = r.json()["access_token"]

    @property
    def _auth(self) -> dict[str, str]:
        if not self._token:
            raise ChatError("нет токена — вызовите login()")
        return {"Authorization": f"Bearer {self._token}"}

    # ── сессии ──────────────────────────────────────────────────────
    def new_session(self, title: str = "benchmark") -> str:
        r = self._http.post(
            f"{self.cfg.api}/chat/sessions", headers=self._auth, json={"title": title}
        )
        if r.status_code != 200:
            raise ChatError(f"create session {r.status_code}: {r.text[:200]}")
        self._session_id = r.json()["id"]
        return self._session_id

    def ensure_session(self) -> str:
        if self.cfg.reuse_session and self._session_id:
            return self._session_id
        return self.new_session()

    # ── сообщение ───────────────────────────────────────────────────
    def ask(self, content: str, mode: str = "auto") -> ChatTurn:
        session_id = self.ensure_session()
        body: dict[str, Any] = {"content": content}
        if mode and mode != "auto":
            body["metadata"] = {"mode": mode}
        t0 = time.perf_counter()
        try:
            r = self._http.post(
                f"{self.cfg.api}/chat/sessions/{session_id}/messages",
                headers={**self._auth, "Accept": "text/event-stream"},
                json=body,
            )
            dt = time.perf_counter() - t0
            if r.status_code != 200:
                return ChatTurn(False, dt, {}, f"HTTP {r.status_code}: {r.text[:200]}")
            return ChatTurn(True, dt, _parse_sse(r.text))
        except (httpx.HTTPError, ChatError) as exc:
            return ChatTurn(False, time.perf_counter() - t0, {}, str(exc))

    def ask_fresh(self, content: str, mode: str = "auto") -> ChatTurn:
        """Задать вопрос в СВЕЖЕЙ сессии, не трогая self._session_id — потокобезопасно
        (httpx.Client разделяем) и без загрязнения историей между вопросами."""
        t0 = time.perf_counter()
        try:
            s = self._http.post(
                f"{self.cfg.api}/chat/sessions", headers=self._auth, json={"title": "b"}
            )
            if s.status_code != 200:
                return ChatTurn(False, 0.0, {}, f"session {s.status_code}: {s.text[:120]}")
            sid = s.json()["id"]
            body: dict[str, Any] = {"content": content}
            if mode and mode != "auto":
                body["metadata"] = {"mode": mode}
            r = self._http.post(
                f"{self.cfg.api}/chat/sessions/{sid}/messages",
                headers={**self._auth, "Accept": "text/event-stream"},
                json=body,
            )
            dt = time.perf_counter() - t0
            if r.status_code != 200:
                return ChatTurn(False, dt, {}, f"HTTP {r.status_code}: {r.text[:200]}")
            return ChatTurn(True, dt, _parse_sse(r.text))
        except (httpx.HTTPError, ChatError) as exc:
            return ChatTurn(False, time.perf_counter() - t0, {}, str(exc))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ChatClient":
        self.login()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def probe_health(cfg: BenchConfig) -> dict[str, Any]:
    """Best-effort проверка доступности бэкенда и (если проброшены) сайдкаров."""
    out: dict[str, Any] = {}
    with httpx.Client(timeout=5.0, trust_env=False) as h:
        # backend
        backend_ok = False
        for path in (
            "/api/v1/utils/health-check/",
            "/api/v1/utils/health-check",
            "/docs",
        ):
            try:
                r = h.get(f"{cfg.base_url}{path}")
                if r.status_code < 500:
                    backend_ok = True
                    break
            except httpx.HTTPError:
                continue
        out["backend"] = backend_ok
        # опциональные прямые пробы (internal-only сайдкары)
        for name, url in (
            ("ontology_kg", cfg.ontology_url),
            ("science_kg", cfg.science_url),
        ):
            if not url:
                out[name] = None
                continue
            ok = False
            for path in ("/api/v1/health", "/health"):
                try:
                    r = h.get(f"{url.rstrip('/')}{path}")
                    ok = r.status_code == 200
                    if ok:
                        break
                except httpx.HTTPError:
                    continue
            out[name] = ok
    return out
