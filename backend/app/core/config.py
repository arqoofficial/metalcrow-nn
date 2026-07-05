import secrets
import warnings
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    # Redis — Celery broker (svc-parse-docling) + query cache
    REDIS_URL: str = "redis://localhost:6379/0"

    # nornickel-2026-parser — single file source (SHARED/ via HTTP)
    PARSER_URL: str = "http://parser-main:8114"
    PARSER_TIMEOUT_S: float = 60.0
    PARSER_POLL_INTERVAL_S: float = 3.0
    PARSER_POLL_TIMEOUT_S: float = 900.0
    PARSER_UPLOAD_WAIT_S: float = 30.0

    # Neo4j — опциональный граф (SPEC_V3 §3 п.8, P1). Недоступен -> 503/SQL fallback
    # в app/services/graph.py, а не падение приложения.
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changethis"

    # science-knowledge-graph — internal-only sidecar (spaCy NER + Neo4j GraphRAG,
    # свой Neo4j-граф, services/science-knowledge-graph/README.md). Недоступен ->
    # graph.run_template/agent.generate_hypothesis деградируют до пустого
    # результата/старого stub-текста, как и Neo4j выше.
    SCIENCE_KG_URL: str = "http://science-knowledge-graph:8000"

    # ontology-knowledge-graph — internal-only sidecar (типизированная онтология
    # на Postgres: провенанс-цитаты, Comparability Gate, evidence/gaps/
    # contradictions/experts; ontology/README.md). Недоступен -> клиент
    # (app/services/ontology_client.py) деградирует до пустого результата.
    ONTOLOGY_KG_URL: str = "http://ontology-knowledge-graph:8000"

    # ── ReAct-агент (SPEC_V3 §5.7) ────────────────────────────────────────────
    # Основной способ ответа chat: агент планирует вызовы тулов ретрива (онтология
    # /manifest + граф знаний) и синтезирует claims с провенансом. Включён по
    # умолчанию; при пустом LLM URL/ключе ИЛИ любой ошибке LLM chat автоматически
    # откатывается на детерминированный водопад (app/services/chat.py::
    # _waterfall_answer) — то есть агент не может «сломать» ответы, только обогатить.
    # OpenAI-совместимый LLM-эндпоинт, общий для сайдкаров (онтология читает эти же
    # LLM_BASE_URL/LLM_API_KEY/LLM_INTENT_MODEL из общего .env). Агент по умолчанию
    # переиспользует их — один подключённый LLM-провайдер на весь стек. Значения
    # задаются через .env (services/llm-gateway или иной OpenAI-совместимый шлюз).
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_INTENT_MODEL: str = "openai/gpt-oss-120b"

    # Выделенный gateway агента (services/llm-gateway). Если пусто — агент падает
    # назад на общие LLM_BASE_URL/LLM_API_KEY/LLM_INTENT_MODEL выше. Префикс
    # LLM_AGENT_* отделён, чтобы при желании дать агенту свой эндпоинт/модель, не
    # трогая сайдкары.
    LLM_AGENT_ENABLED: bool = True  # False — жёстко только прежний водопад
    LLM_AGENT_GATEWAY_URL: str = ""  # напр. http://llm-gateway:4100/v1 (без / на конце)
    LLM_AGENT_API_KEY: str = ""
    LLM_AGENT_MODEL: str = ""
    LLM_AGENT_TIMEOUT_S: float = 60.0
    # Бюджет ReAct-цикла: максимум шагов планировщика и общий дедлайн по стене
    # (спека требует ответ < 15s; при превышении — принудительный синтез из уже
    # собранного).
    LLM_AGENT_MAX_STEPS: int = 3  # раундов планировщика (в каждом — тулы параллельно)
    LLM_AGENT_MAX_PARALLEL_TOOLS: int = 4  # тулов за раунд (диспатчатся конкурентно)
    LLM_AGENT_DEADLINE_S: float = 25.0
    # reasoning-модели (gpt-oss и т.п.) «думают» по умолчанию и жгут токены/латенси;
    # low останавливает переусердствование. Пусто — параметр не отправляется
    # (для моделей без reasoning_effort).
    LLM_AGENT_REASONING_EFFORT: str = "low"

    # litsearch → chat integration (design doc §8). article-fetcher's real port is
    # 8200 (Dockerfile), not the generic 8000 used by the other sidecars above.
    ARTICLE_FETCHER_URL: str = "http://article-fetcher:8200"
    OPENALEX_MAILTO: str = ""
    # LLM for the litsearch tool loop (OpenAI-compatible gateway; LiteLLM +
    # Langfuse, spec §2.8). Named LITSEARCH_* (not the generic LLM_*) so it is
    # unambiguously the LITERATURE pipeline's model — distinct from the
    # science-knowledge-graph's OPENAI_* and the ontology sidecar's own LLM_*
    # gateway vars. Empty LITSEARCH_BASE_URL keeps the feature inert (no
    # fabricated answer — `llm.chat` degrades explicitly). The committed default
    # points at the shared gateway; override per-env via LITSEARCH_BASE_URL /
    # LITSEARCH_LLM_MODEL / LITSEARCH_API_KEY.
    LITSEARCH_BASE_URL: str = "https://llm.autumn-lab.uk/v1"
    LITSEARCH_API_KEY: str = ""
    LITSEARCH_LLM_MODEL: str = "deepseek/deepseek-v4-flash__or"
    # Client read-timeout for one gateway call. The Phase-B read answer can take
    # ~80s+ to generate over a large (100k+ token) full-text context — at 60s the
    # httpx read timed out and DISCARDED a perfectly good answer the model had
    # already produced, degrading the turn ("LLM недоступен"). 180s leaves ample
    # margin. (A faster model would let this drop back down.)
    LITSEARCH_LLM_TIMEOUT: int = 180
    LITSEARCH_MAX_RESULTS: int = 5
    LITSEARCH_MAX_ROUNDS: int = 2
    # Max `litsearch_search` calls per Phase-A question; beyond it the loop forces an abstract-only reply.
    # Kept as the global fallback (unused by chat.py's Phase A now that EN/RU
    # have independent per-tool caps below — chat.py passes
    # `max_successful_by_tool`, not this, so the two caps stack rather than
    # sharing one budget). Still available for any other caller that wants a
    # single combined cap.
    LITSEARCH_MAX_SEARCHES: int = 3
    # Per-tool successful-search caps for Phase A's `literature_search_en` /
    # `literature_search_ru` tools (each counted independently — a turn may
    # use up to EN + RU successful searches total, not one shared budget).
    LITSEARCH_MAX_SEARCHES_EN: int = 3
    LITSEARCH_MAX_SEARCHES_RU: int = 3
    # Cap on *successful* (>=1 paper) literature searches per chat turn. A
    # larger LITSEARCH_MAX_SEARCH_ATTEMPTS bounds total attempts so a run of
    # empty/failed queries can't loop forever while still allowing retries.
    LITSEARCH_MAX_SEARCH_ATTEMPTS: int = 6
    LITSEARCH_FULLTEXT_CHAR_CAP: int = 60000
    # Running read budget (chars): the max total full text the read tool will
    # hand the model across ALL its reads in one Phase-B turn. The model reads
    # papers selectively (by idx); the tool tracks chars returned so far and,
    # once the NEXT read would exceed this budget, returns a "read budget
    # exhausted — answer now, stop calling tools" note instead of more text.
    # This bounds the context so a big union can't overflow / time out the read
    # call (which degraded turns). Live evidence: ~720k chars processed fine,
    # ~1.5M timed out — 500k leaves comfortable room for the prompt + answer.
    LITSEARCH_READ_BUDGET_CHARS: int = 500000
    LITSEARCH_FETCH_TIMEOUT: int = 180
    # Phase B heartbeat: while a search's PDFs are still downloading, the
    # `agent_continue` task re-enqueues itself every N seconds (releasing the
    # worker) instead of blocking, until every paper is terminal (or
    # LITSEARCH_FETCH_TIMEOUT elapses from the search's creation). Only then does
    # it inject the "papers downloaded — read them" turn and run the read loop.
    LITSEARCH_HEARTBEAT_SECONDS: int = 8
    # AM §15 gate: false=stage L0 only (B), true=full graph/ontology ingest (A).
    # Add-to-DB hard ingest (option A): «Добавить в базу» runs the full
    # hard-parse -> graph/ontology pipeline (enqueue_l1_parse). OSN signed off
    # 2026-07-04 -> default True. Set False to stage the Document at L0 only.
    LITSEARCH_INGEST_ENABLED: bool = True

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore
