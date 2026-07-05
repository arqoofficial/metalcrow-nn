# Documentation Requirements

Documentation policy for `ADVANCE_RAG`.

## In Scope

- Required docs for API, services, data, config, infrastructure, and operations.
- Update rules when behavior changes.
- Consistency rules between documents.

## Out Of Scope

- Documentation policy for other projects in repository root.
- Code-style rules not related to documentation.

## Required Documents

- `SPECIFICATION.md`
- `LAYER_PRESENTATION.md`
- `LAYER_SERVICES.md`
- `LAYER_DATA.md`
- `LAYER_CONFIG.md`
- `LAYER_INFRASTRUCTURE.md`
- `ADMIN_PANEL.md`
- `MCP_SERVER.md`
- `MCP_SERVER_TEST.md`
- `TESTING.md`

## Sync Rules

- API behavior changes must update `LAYER_PRESENTATION.md` and `SPECIFICATION.md`.
- Queue or topology changes must update `LAYER_SERVICES.md`.
- Metadata or source-folder rules must update `LAYER_DATA.md`.
- `.env` or `config.yaml` contract changes must update `LAYER_CONFIG.md`.
- Docker or observability changes must update `LAYER_INFRASTRUCTURE.md`.
- Panel command behavior changes must update `ADMIN_PANEL.md`.
- MCP tool contracts must update `MCP_SERVER.md`.
- MCP test scenario changes must update `MCP_SERVER_TEST.md`.
- Test strategy or endpoint behavior changes must update `TESTING.md`.

## Endpoint Documentation Rule

Each endpoint contract in `LAYER_PRESENTATION.md` must include:

- request schema table
- response schema table
- status and error code semantics
- one `curl` example

## Data Model Rule

- Data contract schemas must use Pydantic `BaseModel`.
- Do not use `@dataclass` for API or shared contract models.

## Consistency Rules

- API version must be written as `v1` consistently across docs.
- Default query source subfolder must be documented as `01_docling_clean00`.
- Default query `limit` must be documented as `10`.
- Allowed source subfolder list must be described as configuration-driven via `config.yaml`.
- Unlisted `SHARED` folders must be documented as disallowed for query/index selection.
