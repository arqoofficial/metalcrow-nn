"""Тонкий OpenAI-совместимый chat-completions клиент для litsearch tool loop'а
(design doc §4.2, "litsearch → chat integration").

`chat()` — единый транспорт для нативного OpenAI tool-calling (agent loop,
spec §2.1): пробрасывает `tools`/`tool_choice`, прокидывает Langfuse-metadata
(session_id и т.п.) через top-level `metadata` в теле запроса (LiteLLM
forwards it to Langfuse), и возвращает `ChatResult` с явным `ok` — вызывающая
сторона НИКОГДА не путает `content=None` с реальным ответом модели.

"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """Результат одного раунда gateway chat-completions через `chat()`.
    `ok=False` — единственный явный сигнал отказа (ошибка транспорта/HTTP,
    пустой `LLM_BASE_URL` или неразбираемый tool-call payload). Вызывающая
    сторона НИКОГДА не трактует `content=None` как сфабрикованный ответ."""

    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    temperature: float = 0.2,
    metadata: dict[str, Any] | None = None,
) -> ChatResult:
    """Нативный OpenAI tool-calling раунд к gateway (design doc §2.1, agent
    loop). Единственный транспорт для tool-calling — используется
    `agent/loop.py::run_loop`, а не прежними JSON-synthesis хелперами выше.

    Пробрасывает `tools`/`tool_choice` как есть. `metadata` кладётся
    top-level ключом `metadata` в теле запроса (LiteLLM/Langfuse-конвенция —
    LiteLLM форвардит его в Langfuse для трейс-атрибуции, напр. session_id).

    Возвращает разобранный `content` (может быть `None`, когда модель отдаёт
    только tool calls) и `tool_calls` в виде `[{"id", "name", "arguments": dict}]`.
    Любая ошибка транспорта/HTTP/парсинга -> `ChatResult(content=None,
    tool_calls=[], ok=False)` — фича никогда не фабрикует ответ."""
    if not settings.LITSEARCH_BASE_URL:
        return ChatResult(content=None, tool_calls=[], ok=False)
    payload: dict[str, Any] = {
        "model": settings.LITSEARCH_LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if metadata is not None:
        payload["metadata"] = metadata
    try:
        resp = httpx.post(
            f"{settings.LITSEARCH_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.LITSEARCH_API_KEY}"},
            json=payload,
            timeout=settings.LITSEARCH_LLM_TIMEOUT,
            trust_env=False,
        )
        resp.raise_for_status()
        data: Any = resp.json()
        message = data["choices"][0]["message"]
        content = message.get("content")
        content = content if isinstance(content, str) else None
        raw_calls = message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []
        for call in raw_calls:
            fn = call["function"]
            tool_calls.append(
                {
                    "id": call.get("id", ""),
                    "name": fn["name"],
                    "arguments": json.loads(fn["arguments"] or "{}"),
                }
            )
        return ChatResult(content=content, tool_calls=tool_calls, ok=True)
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning("LLM chat failed: %s", exc)
        return ChatResult(content=None, tool_calls=[], ok=False)
