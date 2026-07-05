# Configuration Layer

Configuration contract for all services in the parser system.

This layer defines:

- where secrets live,
- where non-secret system config lives,
- how every service reads configuration,
- how worker counts are controlled.

Implements [SPECIFICATION.md](SPECIFICATION.md). Related: [LAYER_SERVICES.md](LAYER_SERVICES.md), [LAYER_PRESENTATION.md](LAYER_PRESENTATION.md).

---

## Sources of configuration

### 1) Secrets: `.env`

Store sensitive values only in `.env`.

Examples of secrets:

- Redis password / full Redis URL with credentials
- API keys for external tools
- private tokens used by integrations

Rules:

- `.env` is not committed to git.
- `.env.example` may be committed without secrets.
- Services read `.env` at startup (or via process environment injected from it).

### 2) System config: `config.yaml`

Store full non-secret system configuration in `config.yaml`.

Examples:

- paths
- queue names
- enabled stages
- worker counts
- service host/port
- timeouts and retries

Rules:

- `config.yaml` is the main source for runtime behavior.
- It can be committed (no secrets inside).
- Every service loads the same file for shared values.

---

## Required files

| File | Purpose | Secret data |
|------|---------|-------------|
| `.env` | Sensitive values | yes |
| `.env.example` | Template for local/dev setup | no |
| `config.yaml` | Full system runtime config | no |

---

## Loading order

1. Load `config.yaml`.
2. Load `.env`.
3. Resolve `${ENV_VAR}` references from `config.yaml` using environment.
4. Validate final config with Pydantic models before service starts.

If validation fails, service must not start.

---

## Worker count is a critical parameter

Worker count directly controls throughput and load.

It must be explicit in `config.yaml` and validated as integer `>= 1`.

Recommended keys:

```yaml
workers:
  raw2docling_raw: 2
  docling_raw2docling_clean00: 4
```

Runtime requirements:

- counts are mandatory (no hidden defaults in code),
- startup logs must print effective worker counts,
- deploy scripts should start exactly that number of worker processes.

---

## Suggested `config.yaml` shape

```yaml
shared_root: /mnt/nfs/SHARED

queues:
  raw2docling_raw: parser:jobs:raw2docling_raw
  docling_raw2docling_clean00: parser:jobs:docling_raw2docling_clean00

api:
  host: 0.0.0.0
  port: 8114

workers:
  raw2docling_raw: 4
  docling_raw2docling_clean00: 4

locks:
  upload_suffix: .upload.lock
  worker_suffix: .worker.lock

pipeline:
  stages:
    - docling_raw
    - docling_clean00
  docling:
    ocr_enabled: true
    ocr_languages: [en, ru]

runtime:
  process_timeout_seconds: 600

observability:
  metrics_enabled: false
  otel_enabled: false
  langfuse_enabled: false

admin_panel:
  api_base_url: http://127.0.0.1:8114
  actions:
    allow_reindex: true
    allow_restart_hooks: false
    restart_hooks_script: rerun.sh
    confirm_destructive_actions: true
```

See [DOCLING.md](DOCLING.md) for Docling settings. Local dev panel config: `config/local.yaml` (used by `./panel.sh` when `config.yaml` is absent).

---

## Suggested `.env` shape

```dotenv
# Redis
REDIS_URL=redis://:password@redis-host:6379/0

# Optional integration secrets
ADMIN_PANEL_API_TOKEN=
OTEL_EXPORTER_OTLP_ENDPOINT=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

Note: if `REDIS_URL` contains credentials, keep it only in `.env` and reference it from code directly (not as plain value in `config.yaml`).

---

## Service responsibilities

| Service | Reads `.env` | Reads `config.yaml` | Key config responsibilities |
|---------|---------------|---------------------|-----------------------------|
| `service/main` | yes | yes | API host/port, shared paths, queue names, lock behavior |
| `service/raw2docling_raw` | yes | yes | stage queue, shared paths, stage timeout |
| `service/docling_raw2docling_clean00` | yes | yes | stage queue, shared paths, cleanup settings |

Worker count values are used by process launcher/orchestration, not by a single worker process itself.

---

## Validation model

Use strict Pydantic models for config validation.

Minimum checks:

- required fields present,
- worker counts are positive integers,
- queue names are non-empty,
- `shared_root` exists and is writable,
- no secret fields are allowed in `config.yaml`.

---

## Operational notes

- Changing worker counts must be a conscious operation and visible in deployment diff.
- For full-system restart, use existing restart scripts and `clean_lock.sh`.
- Missing lock files during cleanup are not fatal.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Added `pipeline.docling`, `observability`, `admin_panel`; local dev config note |
| 2026-07-03 | Initial configuration layer: `.env` for secrets, `config.yaml` for system settings, worker-count contract |
