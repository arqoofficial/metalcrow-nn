# Infrastructure Layer

Runtime and observability contract for `ADVANCE_RAG`.

## In Scope

- Docker runtime model.
- Docker Compose startup orchestration.
- `uv` package manager contract.
- Prometheus, OpenTelemetry, and Loguru contracts.

## Out Of Scope

- Kubernetes and Helm deployment design.
- External observability backend provisioning policy.
- Cross-project infrastructure standardization.

## Docker Startup Model

- Service is containerized and runs with Docker.
- Runtime configuration comes from `.env` and `config.yaml`.
- Container startup must validate config before serving API.

## Package Manager Contract

- `uv` is the required package manager for this project.
- Dependency installation and environment sync use `uv`.

## Docker Compose Startup Model

- Docker Compose is the default local and single-host orchestration.
- Compose starts API process, queue worker, and required dependencies.
- Compose wiring must support `SHARED` access and service scripts.
- `panel-docker.sh` is used as operator entrypoint for Docker-controlled behavior.

## Logging Contract

- Loguru is the application logger.
- Logs include enough context to trace API requests and index jobs.
- Logging remains available even when telemetry exporters are unavailable.

## Metrics Contract

- Prometheus metrics are exposed for API and indexing flows.
- Metrics include request outcomes and indexing worker behavior.
- Metric labels remain low-cardinality.

## Tracing Contract

- OpenTelemetry is used for distributed traces.
- Request traces cover query and indexing flows.
- Queue-based indexing spans connect API acceptance and worker execution.

## Failure Behavior

- Core API and indexing remain operational in degraded mode if telemetry exporters are down.
- Observability failures do not block service startup when core dependencies are healthy.
