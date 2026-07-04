"""Конфигурация прогона: URL контура, учётка, пороги, LLM-судья.

Значения берутся (в порядке приоритета) из аргументов CLI → переменных
окружения → `metalcrow/.env` → дефолтов. Учётка для логина — та же, что у
бэкенда (`FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_REPO_DIR = _PKG_DIR.parent  # metalcrow/
DEFAULT_DATASET = _PKG_DIR / "data" / "questions.yaml"
DEFAULT_OUT_DIR = _PKG_DIR / "results"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Мини-парсер .env (KEY=VALUE, кавычки, комментарии). Без зависимостей."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def _env_lookup(dotenv: dict[str, str], *keys: str, default: str = "") -> str:
    for k in keys:
        if os.environ.get(k):
            return os.environ[k]
    for k in keys:
        if dotenv.get(k):
            return dotenv[k]
    return default


@dataclass
class BenchConfig:
    base_url: str = "http://localhost:8000"
    username: str = "admin@example.com"
    password: str = "changethis"
    # Прямые пробы опциональных сайдкаров (internal-only; заполнять только если
    # их порты проброшены наружу — иначе доступность выводится из tools_used).
    ontology_url: str = ""
    science_url: str = ""

    dataset_path: Path = DEFAULT_DATASET
    out_dir: Path = DEFAULT_OUT_DIR

    # что отправлять
    modes: list[str] = field(default_factory=lambda: ["auto"])
    use_expected_mode: bool = False

    # оценка
    latency_target_s: float = 5.0  # НФТ из ТЗ: сложный запрос за 3–5 c
    pass_threshold: float = 0.6  # порог «вопрос зачтён» по итоговому score
    fail_under: float | None = None  # ненулевой exit-code, если overall ниже

    # транспорт
    timeout_s: float = 120.0
    reuse_session: bool = True

    # LLM-судья (опционально, OpenAI-совместимый эндпоинт)
    judge: bool = False
    judge_base_url: str = ""
    judge_api_key: str = ""
    judge_model: str = "gpt-4o-mini"

    @classmethod
    def load(cls, **overrides: object) -> "BenchConfig":
        dotenv = _parse_env_file(_REPO_DIR / ".env")
        cfg = cls(
            username=_env_lookup(
                dotenv, "BENCH_USERNAME", "FIRST_SUPERUSER", default="admin@example.com"
            ),
            password=_env_lookup(
                dotenv,
                "BENCH_PASSWORD",
                "FIRST_SUPERUSER_PASSWORD",
                default="changethis",
            ),
            judge_base_url=_env_lookup(
                dotenv, "BENCH_JUDGE_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL"
            ),
            judge_api_key=_env_lookup(
                dotenv, "BENCH_JUDGE_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"
            ),
            judge_model=_env_lookup(
                dotenv, "BENCH_JUDGE_MODEL", "OPENAI_MODEL", default="gpt-4o-mini"
            ),
        )
        base = _env_lookup(dotenv, "BENCH_BASE_URL")
        if base:
            cfg.base_url = base
        for key, val in overrides.items():
            if val is None:
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, val)
        cfg.base_url = cfg.base_url.rstrip("/")
        cfg.dataset_path = Path(cfg.dataset_path)
        cfg.out_dir = Path(cfg.out_dir)
        return cfg

    @property
    def api(self) -> str:
        return f"{self.base_url}/api/v1"
