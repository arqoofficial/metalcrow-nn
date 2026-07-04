# -*- coding: utf-8 -*-
"""
LLM-экстракция фактов из технического текста (стадия B конвейера).

Эндпоинт — любой OpenAI-совместимый (адрес и ключ — env LLM_BASE_URL /
LLM_API_KEY, либо те же переменные в .env корня репозитория). Извлечение
строго по JSON-схеме через response_format json_schema strict.

Модели: основная — экстракционная (NuExtract3); фолбэк — универсальная
с reasoning_effort=low. Список моделей меняется — проверяется через
models.list() при старте.

Промпт нейтральный: только задача извлечения, без каких-либо сведений
о том, где и зачем это используется.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Optional

PRIMARY_MODEL = "Numind/NuExtract3"
FALLBACK_MODEL = "Openai/Gpt-oss-120b"
BASE_URL = os.environ.get("LLM_BASE_URL", "")

# ── JSON-схема выхода (strict: everything required, no additionalProperties) ─

def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props,
            "required": required if required is not None else list(props),
            "additionalProperties": False}


_MEAS = _obj({
    "property": {"type": "string", "description": "measured quantity name as in text"},
    "value": {"type": "string", "description": "value exactly as written: '95-97', '<=1000', '229 ± 7'"},
    "unit": {"type": "string"},
    "material": {"type": "string", "description": "which material/substance this value belongs to; empty if the whole experiment"},
    "quote": {"type": "string", "description": "verbatim sentence from the text containing this value"},
})

_CONCL = _obj({
    "text": {"type": "string", "description": "one-sentence factual conclusion"},
    "kind": {"type": "string", "enum": ["finding", "recommendation"]},
    "property": {"type": "string", "description": "affected quantity; empty if none"},
    "direction": {"type": "string",
                  "enum": ["increases", "decreases", "no_change", "nonmonotonic", ""]},
    "factor": {"type": "string", "description": "what was varied/what causes the effect; empty if none"},
    "quote": {"type": "string", "description": "verbatim sentence from the text"},
})

_MATUSE = _obj({
    "name": {"type": "string"},
    "role": {"type": "string",
             "enum": ["sample", "input", "output", "medium", "flux", "atmosphere", "reference"]},
})

_EXP = _obj({
    "label": {"type": "string", "description": "short label of the experiment/process run"},
    "process": {"type": "string", "description": "operation name as in text (e.g. выщелачивание, обжиг)"},
    "materials": {"type": "array", "items": _MATUSE},
    "temperature": {"type": "string", "description": "process temperature as written, empty if absent"},
    "duration": {"type": "string", "description": "process duration as written, empty if absent"},
    "measurements": {"type": "array", "items": _MEAS},
    "conclusions": {"type": "array", "items": _CONCL},
    "quote": {"type": "string", "description": "verbatim sentence introducing this experiment"},
})

_CLAIM = _obj({
    "text": {"type": "string", "description": "one-sentence engineering statement"},
    "kind": {"type": "string", "enum": ["finding", "recommendation"]},
    "process": {"type": "string", "description": "technology/method the statement is about; empty if none"},
    "property": {"type": "string", "description": "affected quantity; empty if none"},
    "direction": {"type": "string",
                  "enum": ["increases", "decreases", "no_change", "nonmonotonic", ""]},
    "factor": {"type": "string", "description": "what causes the effect; empty if none"},
    "quote": {"type": "string", "description": "verbatim sentence from the text"},
})

EXTRACTION_SCHEMA = _obj({
    "experiments": {"type": "array", "items": _EXP},
    "claims": {"type": "array", "items": _CLAIM},
})

PROMPT = (
    "Extract structured facts from the technical text below (Russian or English). "
    "Follow the JSON schema exactly.\n"
    "Into 'experiments' put concrete activities with conditions and results: laboratory "
    "experiments, pilot tests, AND industrial application cases of a technology at a "
    "specific site or plant.\n"
    "Into 'claims' put standalone engineering statements not tied to a described "
    "experiment: applicability conditions of a method, operating experience, typical "
    "performance or cost figures, recommendations from reviews.\n"
    "Rules: "
    "(1) every measurement, conclusion and claim MUST carry 'quote' — a verbatim "
    "sentence copied character-for-character from the text; "
    "(2) copy numeric values as written, do not convert units; "
    "(3) only facts stated in the text, never invent; "
    "(4) if nothing found, return empty arrays.\n\n"
    "TEXT:\n"
)


# ── клиент ───────────────────────────────────────────────────────────────

def _dotenv(name: str) -> str | None:
    """Ключи ротируются: .env репозитория — источник правды (читается первым),
    env-переменная процесса — фолбэк (в долгих сессиях шелла она протухает).
    Ищем .env вверх по дереву: пакет живёт в services/<svc>/ontology."""
    for up in Path(__file__).resolve().parents:
        env = up / ".env"
        if env.exists():
            m = re.search(rf"^{name}=(.+)$", env.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1).strip().strip('"')
        if (up / ".git").exists():
            break
    return None


def _read_base_url() -> str:
    url = _dotenv("LLM_BASE_URL") or BASE_URL
    if not url:
        raise RuntimeError("LLM_BASE_URL не задан ни в .env, ни в env")
    return url


def _read_env_key() -> str:
    key = _dotenv("LLM_API_KEY") or os.environ.get("LLM_API_KEY")
    if key:
        return key
    raise RuntimeError("LLM_API_KEY не найден ни в .env, ни в env")


class Extractor:
    """Потокобезопасный экстрактор: один клиент, параллельные вызовы."""

    def __init__(self, model: str | None = None, max_tokens: int = 3000,
                 timeout: float = 120.0):
        from openai import OpenAI
        self.client = OpenAI(base_url=_read_base_url(), api_key=_read_env_key(),
                             timeout=timeout, max_retries=2)
        self.max_tokens = max_tokens
        self._lock = threading.Lock()
        available = {m.id for m in self.client.models.list()}
        if model and model in available:
            self.model = model
        elif PRIMARY_MODEL in available:
            self.model = PRIMARY_MODEL
        elif FALLBACK_MODEL in available:
            self.model = FALLBACK_MODEL
        else:
            raise RuntimeError(f"нет подходящей модели; доступны: {sorted(available)}")

    def extract_chunk(self, text: str) -> dict:
        """Один чанк → {'experiments': [...]}; исключения пробрасываются."""
        kwargs: dict = dict(
            model=self.model, temperature=0, max_tokens=self.max_tokens,
            response_format={"type": "json_schema", "json_schema": {
                "name": "extraction", "schema": EXTRACTION_SCHEMA, "strict": True}},
            messages=[{"role": "user", "content": PROMPT + text}])
        if self.model == FALLBACK_MODEL:
            kwargs["reasoning_effort"] = "low"
        r = self.client.chat.completions.create(**kwargs)
        content = r.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data.get("experiments"), list):
            data["experiments"] = []
        if not isinstance(data.get("claims"), list):
            data["claims"] = []
        return data

    def warmup(self) -> None:
        """Холодный старт модели 30–50 с — один короткий прогревочный вызов."""
        try:
            self.client.chat.completions.create(
                model=self.model, max_tokens=8, temperature=0,
                messages=[{"role": "user", "content": "ok"}])
        except Exception:
            pass
