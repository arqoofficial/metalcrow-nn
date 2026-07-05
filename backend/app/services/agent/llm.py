"""OpenAI-совместимый клиент к LLM-gateway (services/llm-gateway, LiteLLM).

Тонкая обёртка над `httpx` (уже в зависимостях — openai-SDK не нужен). Транспорт
структурированного вывода — `response_format=json_schema` (как в
`ontology/mocks/intent_llm.py`), с одним ретраем на невалидный JSON и мягким
извлечением JSON из markdown/префиксов. Это работает на всех моделях gateway
независимо от поддержки нативного tool-calling.

Любая недоступность/некорректный ответ поднимается как `LLMUnavailable`, что выше
по стеку (`chat.answer_message`) откатывает диалог на прежний водопад — включение
агента не может ухудшить ответы, только обогатить.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """LLM-gateway выключен/не сконфигурирован, недоступен или вернул мусор."""


# Выделенный gateway агента имеет приоритет; иначе — общий LLM-эндпоинт сайдкаров
# (те же LLM_BASE_URL/LLM_API_KEY/LLM_INTENT_MODEL, что у онтологии). Так локально
# «всё через один подключённый API», а под свой gateway достаточно задать LLM_AGENT_*.
def _base_url() -> str:
    return settings.LLM_AGENT_GATEWAY_URL or settings.LLM_BASE_URL


def _api_key() -> str:
    return settings.LLM_AGENT_API_KEY or settings.LLM_API_KEY


def _model() -> str:
    return settings.LLM_AGENT_MODEL or settings.LLM_INTENT_MODEL


def is_configured() -> bool:
    """Включён ли агент и есть ли куда/чем ходить. Дешёвая проверка перед циклом."""
    return bool(settings.LLM_AGENT_ENABLED and _base_url() and _api_key())


def _post(messages: list[dict[str, Any]], *, response_format: dict[str, Any] | None) -> str:
    if not (_base_url() and _api_key()):
        raise LLMUnavailable("LLM endpoint URL/key not configured")
    payload: dict[str, Any] = {
        "model": _model(),
        "messages": messages,
        "temperature": 0.0,
    }
    if settings.LLM_AGENT_REASONING_EFFORT:
        payload["reasoning_effort"] = settings.LLM_AGENT_REASONING_EFFORT
    if response_format is not None:
        payload["response_format"] = response_format
    url = f"{_base_url().rstrip('/')}/chat/completions"
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {_api_key()}"},
            timeout=httpx.Timeout(settings.LLM_AGENT_TIMEOUT_S, connect=5.0),
            trust_env=False,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"gateway request failed: {exc}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable(f"malformed gateway response: {exc}") from exc
    return str(content or "")


def complete_text(system: str, user: str) -> str:
    """Свободный текстовый ответ (без схемы)."""
    return _post(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=None,
    )


def complete_json(
    system: str,
    user: str,
    *,
    schema: dict[str, Any],
    schema_name: str = "output",
    strict: bool = True,
) -> dict[str, Any]:
    """Строгий JSON-вывод по схеме. Пытается `response_format=json_schema`; если
    провайдер его проигнорировал (LiteLLM `drop_params`) или модель вернула
    невалидный JSON — один ретрай с явным требованием «только JSON». Иначе
    `LLMUnavailable`.

    `strict=False` — для схем со свободной формой (напр. `args` планировщика с
    произвольными ключами), которые strict-режим OpenAI/Yandex отверг бы."""
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": strict},
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    raw = _post(messages, response_format=response_format)
    parsed = _try_parse(raw)
    if parsed is not None:
        return parsed

    messages.append({"role": "assistant", "content": raw})
    messages.append(
        {
            "role": "user",
            "content": "Верни СТРОГО валидный JSON по схеме — без markdown, "
            "пояснений и текста вокруг.",
        }
    )
    raw_retry = _post(messages, response_format=response_format)
    parsed_retry = _try_parse(raw_retry)
    if parsed_retry is None:
        raise LLMUnavailable("model did not return valid JSON")
    return parsed_retry


def _try_parse(raw: str) -> dict[str, Any] | None:
    """Снять ```json``` обёртку / выдрать первый {...}-блок и распарсить в dict."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()
    obj = _loads_or_none(text)
    if obj is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            obj = _loads_or_none(text[start : end + 1])
    return obj if isinstance(obj, dict) else None


def _loads_or_none(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
