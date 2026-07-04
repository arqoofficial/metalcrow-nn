# Infrastructure Layer

Infrastructure specification for runtime, observability, and future LLM integration.

This document describes:

- container runtime with Docker,
- local/dev orchestration with Docker Compose,
- structured logging with Loguru,
- metrics collection with Prometheus,
- distributed tracing with OpenTelemetry,
- LLM observability with Langfuse (future scope).

Implements [SPECIFICATION.md](SPECIFICATION.md). Related: [LAYER_SERVICES.md](LAYER_SERVICES.md), [LAYER_CONFIG.md](LAYER_CONFIG.md), [ADMIN_PANEL.md](ADMIN_PANEL.md).

---

## 1. Goals

1. Provide reproducible runtime for all services.
2. Standardize logs, metrics, and traces across components.
3. Make the system debuggable under failures and concurrency.
4. Prepare an integration path for future LLM requests with full telemetry.

---

## 2. Scope

### In scope

- Docker images for all parser services.
- Docker Compose for local/dev and single-host ops.
- Loguru-based structured logs.
- Prometheus scrape targets and metrics naming.
- OpenTelemetry tracing for API and workers.
- Langfuse instrumentation design for future LLM calls.

### Out of scope (current stage)

- Kubernetes manifests and Helm charts.
- Multi-region high availability.
- Production SSO/RBAC for telemetry UIs.
- Mandatory LLM integration in current parser pipeline.

---

## 3. Docker

### Services to containerize

- `service/main`
- `service/raw2docling_raw`
- `service/docling_raw2docling_clean00`
- optional `admin_panel`

### Image requirements

- Base image: slim Python image.
- Non-root runtime user.
- Read-only code layer where possible.
- Runtime configuration from `.env` + `config.yaml`.
- Healthcheck for each service.

### Runtime mounts

- bind or volume for `SHARED/` filesystem.
- bind mount for `config.yaml` (read-only preferred).
- bind mount for `.env` only where needed.

### Build strategy

- One Dockerfile per service or one multi-target Dockerfile.
- Dependencies managed with **uv** (`pyproject.toml` + `uv.lock`).
- Docker images run `uv sync --frozen --no-dev --no-install-project` as user `parser` with a BuildKit cache mount (avoids slow `chown -R` on `.venv`).
- Build args must not contain secrets.

### Local development

```bash
uv sync   # installs docling and all core deps
uv run pytest -q
./panel.sh
```

---

## 4. Docker Compose

Compose is the default local orchestration layer.

### Required components

- `main` API service
- `raw2docling_raw` workers
- `docling_raw2docling_clean00` workers
- `redis`
- `prometheus`
- optional `grafana`
- optional `langfuse` stack for future LLM tracing

### Worker scaling

Worker count is controlled by configuration and/or Compose `--scale`:

- `raw2docling_raw`: critical throughput parameter
- `docling_raw2docling_clean00`: critical throughput parameter

Counts must be explicit in deployment configuration.

### Offline model contract for workers

`raw2docling_raw` and `docling_raw2docling_clean00` use a shared model cache mount:

- host: `./SHARED/MODELS`
- container: `/models`

Required worker env keys:

- `MODEL_CACHE_ROOT=/models`
- `EASYOCR_MODULE_PATH=/models/easyocr`
- `HF_HOME=/models/huggingface`
- `TRANSFORMERS_CACHE=/models/huggingface/transformers`
- `TORCH_HOME=/models/torch`
- `XDG_CACHE_HOME=/models/xdg`
- `REQUIRE_PRELOADED_MODELS=true`

Preload once before offline startup:

```bash
./rerun.sh preload-models
```

When `REQUIRE_PRELOADED_MODELS=true`, workers fail fast if cache/sentinel is missing. They must not download model weights on startup.

### Optional GPU contract for workers

Worker services can request GPU via compose:

- `deploy.resources.reservations.devices` with `driver: nvidia`
- set `DOCKER_GPU_COUNT=1` (or more) to reserve GPUs for worker containers

OCR runtime selector:

- `DOC_OCR_USE_GPU=auto` (default): detect CUDA and fall back to CPU
- `DOC_OCR_USE_GPU=true`: require CUDA, fail fast if unavailable
- `DOC_OCR_USE_GPU=false`: force CPU

Host prerequisites:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### Network and ports

- Internal network for inter-service traffic.
- Expose only necessary host ports:
  - API
  - Prometheus (optional in secure environments)
  - Grafana/Langfuse UI (optional)

### Compose profiles (recommended)

- `core`: parser + redis
- `observability`: prometheus + grafana + otel collector
- `llm`: langfuse-related services

---

## 5. Logging (Loguru)

Loguru is the standard logger for all Python services.

### Requirements

- JSON logs for machine parsing in containers.
- Human-readable logs for local development (optional mode).
- Common fields in every record:
  - `service`
  - `component`
  - `job_id` (if available)
  - `requested_path`
  - `resolved_path`
  - `stage`
  - `level`
  - `timestamp`

### Levels

- `DEBUG`: local troubleshooting only
- `INFO`: normal lifecycle events
- `WARNING`: degraded/non-fatal issues
- `ERROR`: failed operation

### Correlation

Inject correlation IDs into logs:

- request-level id for API calls,
- job-level id for workers.

---

## 6. Metrics (Prometheus)

Prometheus is the source of metrics.

### Endpoint

Each service exposes `/metrics` (or exporter sidecar provides it).

### Required metric families

- HTTP:
  - request count
  - request latency
  - error count
- Queue/worker:
  - jobs enqueued
  - jobs consumed
  - job processing duration
  - queue lag estimate
- Pipeline:
  - outputs produced per stage
  - stage success/fallback counts
- Runtime:
  - process uptime
  - restart count (if available)

### Naming convention

Prefix metrics with `parser_` and keep labels low-cardinality.

Example labels:

- `service`
- `stage`
- `status`

Avoid raw file path as metric label.

---

## 7. Tracing (OpenTelemetry)

OpenTelemetry is the tracing standard (correct spelling; sometimes written as "OpenThelemetry" in drafts).

### Instrumentation targets

- FastAPI request lifecycle in `service/main`.
- Queue enqueue/dequeue operations.
- Worker stage execution spans.
- File I/O critical spans (read raw, write stage outputs).

### Propagation

- Propagate trace context from API request into queued job payload.
- Continue trace in worker when job is consumed.

### Export

- OTLP exporter to an OpenTelemetry Collector.
- Collector forwards to selected backend (Jaeger/Tempo/etc.).

---

## 8. Langfuse (future LLM requests)

Langfuse is reserved for future LLM workflows.

### When to use

Use Langfuse only when parser services start making LLM calls (for enrichment, cleanup assist, classification, etc.).

### Planned telemetry fields

- prompt template/version
- model name
- token usage
- latency
- cost estimate
- request outcome
- correlation ids linking to OpenTelemetry trace and Loguru logs

### Non-goals for current stage

- No Langfuse dependency is required for non-LLM parser flow.
- Do not block core pipeline startup if Langfuse is unavailable.

---

## 9. Config integration

Infrastructure config is defined in `config.yaml`; secrets in `.env`.

Suggested `config.yaml` section:

```yaml
infrastructure:
  logging:
    format: json
    level: INFO
  prometheus:
    enabled: true
    metrics_path: /metrics
  otel:
    enabled: true
    service_name_prefix: parser
    otlp_endpoint: http://otel-collector:4317
  langfuse:
    enabled: false
    host: http://langfuse:3000
```

Suggested `.env` keys:

```dotenv
REDIS_URL=redis://:password@redis:6379/0
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_HOST=http://langfuse:3000
```

---

## 10. Failure behavior

- Observability stack failure must not stop parser core processing.
- Services continue in degraded mode if metrics/traces exporter is down.
- Logging must always remain available at least to stdout/stderr.

---

## 11. Acceptance criteria

1. All parser services run in Docker and are orchestrated by Docker Compose.
2. Logs are structured and include required correlation fields.
3. Prometheus can scrape parser service metrics.
4. OpenTelemetry traces link API request to worker execution.
5. Langfuse integration path is documented and disabled by default.
6. Core parser services remain operational if telemetry backends are unavailable.

---

## 12. Changelog

| Date | Change |
|------|--------|
| 2026-07-03 | Added optional GPU worker contract (`DOCKER_GPU_COUNT`, `DOC_OCR_USE_GPU`) with CPU fallback |
| 2026-07-03 | Added offline worker model preload contract (`./rerun.sh preload-models`) |
| 2026-07-03 | Faster Docker build: parser-owned venv, uv cache mount, no recursive chown |
| 2026-07-03 | Docling core dep; `/health` and `/ready`; `config/local.yaml` for host panel |
| 2026-07-03 | Initial infrastructure layer spec: Docker, Compose, Loguru, Prometheus, OpenTelemetry, Langfuse |
