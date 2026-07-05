# STUBS

## Confirmed stub-like gaps (current)

- `examples/mcp_client_example.py`
  - Uses a plain JSON HTTP call shape for MCP tool calls.
  - This is a simplified transport assumption and may not represent full MCP protocol usage.

- `examples/langchain_from_mcp_example.py`
  - Wraps the same simplified MCP client behavior.
  - Good for local demo structure, but protocol-level interoperability is not guaranteed.

## Operationally permissive fallback (not a hard stub)

- `app/main.py`
  - Chroma initialization is guarded by a broad exception handler and falls back to `chroma_ready=False`.
  - Useful for degraded startup, but can hide root-cause errors without explicit logging.
