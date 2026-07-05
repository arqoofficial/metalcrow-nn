#!/usr/bin/env bash
# Langfuse trace-attribution probe.
#
# Fires ONE chat completion at the LiteLLM gateway with the same `langfuse_*`
# headers (and mirrored body `metadata`) that our services now send, then tells
# you what to look for in the Langfuse UI. This verifies the LEAF of the chain
# (gateway -> Langfuse). The service wiring that FORWARDS these headers is
# unit-tested in each component's tests/test_langfuse_tracing.py.
#
# Verified header contract (docs.litellm.ai/docs/observability/langfuse_integration):
#   the proxy strips the `langfuse_` prefix and maps the header onto the trace —
#     langfuse_trace_user_id -> Langfuse User Id
#     langfuse_session_id    -> Langfuse Session Id
#   The request-body `metadata.{trace_user_id,session_id}` form is equivalent;
#   we send BOTH so attribution lands regardless of the gateway's LiteLLM version.
#
# Usage:
#   LITELLM_MASTER_KEY=sk-... ./scripts/langfuse_trace_probe.sh
#   GATEWAY=http://127.0.0.1:4100 MODEL=openai/gpt-oss-20b USER_ID=alice \
#     SESSION_ID=chat-42 LITELLM_MASTER_KEY=sk-... ./scripts/langfuse_trace_probe.sh
set -euo pipefail

GATEWAY="${GATEWAY:-http://127.0.0.1:4100}"   # LiteLLM gateway base URL
MODEL="${MODEL:-openai/gpt-oss-20b}"          # any model the gateway serves
USER_ID="${USER_ID:-probe-user-42}"
SESSION_ID="${SESSION_ID:-probe-session-abc}"
KEY="${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY (see services/llm-gateway/.env)}"

echo "→ POST ${GATEWAY}/v1/chat/completions   model=${MODEL}"
echo "  langfuse_trace_user_id=${USER_ID}   langfuse_session_id=${SESSION_ID}"
echo

curl -sS --fail-with-body \
  --location "${GATEWAY}/v1/chat/completions" \
  --header "Authorization: Bearer ${KEY}" \
  --header "Content-Type: application/json" \
  --header "langfuse_trace_user_id: ${USER_ID}" \
  --header "langfuse_session_id: ${SESSION_ID}" \
  --header "langfuse_tags: [\"langfuse-probe\"]" \
  --data @- <<JSON | { command -v jq >/dev/null 2>&1 && jq -r '.choices[0].message.content // .' || cat; }
{
  "model": "${MODEL}",
  "messages": [{"role": "user", "content": "ping — langfuse attribution probe"}],
  "max_tokens": 16,
  "metadata": {
    "trace_user_id": "${USER_ID}",
    "session_id": "${SESSION_ID}",
    "tags": ["langfuse-probe"]
  }
}
JSON

cat <<EOF

✓ request sent. Open Langfuse -> Traces and confirm the newest trace shows:
    User Id  = ${USER_ID}
    Session  = ${SESSION_ID}
    Tag      = langfuse-probe
If those appear, the gateway->Langfuse leg is good and the service-forwarded
headers will attribute real chat traces the same way.
EOF
