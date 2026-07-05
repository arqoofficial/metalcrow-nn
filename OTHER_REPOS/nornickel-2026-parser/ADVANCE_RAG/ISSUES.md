# ISSUES

## High-risk / contract-risk issues

- `examples/mcp_client_example.py`
  - MCP example calls use generic JSON-over-HTTP assumptions instead of a validated MCP protocol flow.
  - Risk: example behavior may diverge from real MCP client/server expectations.

- `tests/integration/test_mcp.py`
  - Covers tool mapping and mocked client behavior, but not full live MCP transport execution.
  - Risk: protocol-level issues can pass test suite.

## Medium issues

- `app/main.py`
  - Chroma startup errors are swallowed and surfaced only indirectly through readiness state.
  - This can obscure startup root causes during operations.

- `admin_panel.py`
  - PID tracking can still be cleared while refusing a stop action for mismatched command lines.
  - This may lose process traceability in stale PID-file scenarios.
