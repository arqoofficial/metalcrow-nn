# LiteLLM Gateway (`services/llm-gateway`)

A single OpenAI-compatible endpoint for the fleet, with two layers of redundancy and full
tracing to a shared self-hosted Langfuse. Design spec:
[`docs/superpowers/specs/2026-07-03-litellm-langfuse-gateway-design.md`](../../docs/superpowers/specs/2026-07-03-litellm-langfuse-gateway-design.md).

## What it does

- **Primary:** Yandex (YandexGPT) via its OpenAI-compatible endpoint.
- **Fallback:** OpenRouter.
- **Layer 1 — key failover:** a rate-limited (HTTP 429) API key is cooled down and a
  sibling key for the *same* provider is retried automatically.
- **Layer 2 — provider failover:** if a whole provider fails, the router falls back to the
  other provider (`chat` → `chat-openrouter`).
- **Tracing:** every call (incl. which key/provider served it and whether it fell back) is
  logged to Langfuse with cost, latency, and trace metadata — project-scoped to
  `metalcrow-gateway`, on our own infra.

Agents call models by their **OpenRouter id** (e.g. `openai/gpt-oss-120b`); each is served
Yandex-primary with automatic OpenRouter fallback. Exposed models (on both providers):
`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `deepseek/deepseek-v4-flash`,
`deepseek/deepseek-v3.2`, `qwen/qwen3-235b-a22b`. (Yandex-only models — YandexGPT, Alice —
have no OR name and are not exposed; add them under their own names if needed.)

## Ports

- Proxy: `http://127.0.0.1:4100` (host `:4000` is the existing Langfuse server).

## First run

```bash
cd services/llm-gateway

# 1. Assemble .env from the out-of-repo secrets dir (../../safe relative to repo root).
#    (see the block below; NEVER commit .env)
cp .env.example .env      # then fill values, or use the assembly command below

# 2. Render the template (bakes in the folder id; keys stay as env refs).
./render-config.sh

# 3. Bring it up (attaches the shared Langfuse network).
docker compose up -d
docker compose logs -f litellm      # watch startup
```

### Assembling `.env` from `../safe`

The secrets live outside the repo in `safe/` (sibling of `metalcrow/`). Example one-shot:

```bash
SAFE=../../../safe    # adjust to your checkout; == <repos>/safe
{
  echo "YANDEX_API_KEY_1=$(cat "$SAFE/nornickel_hack_yandex_api_key")"
  echo "YANDEX_FOLDER_ID=$(cat "$SAFE/nornickel_hack_yandex_folder_id")"
  echo "OPENROUTER_API_KEY_1=$(cat "$SAFE/nornickel_hack_or_key")"
  echo "OPENROUTER_API_KEY_2=$(cat "$SAFE/or_key_0")"
  grep -E '^LANGFUSE_(PUBLIC|SECRET)_KEY=' "$SAFE/langfuse-metalcrow.env"
  echo "LANGFUSE_HOST=http://langfuse-server:3000"
  echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)"
} > .env
chmod 600 .env
```

## Smoke test

```bash
# health
curl -s http://127.0.0.1:4100/health/liveliness

# a live completion through the primary (Yandex), traced to Langfuse
curl -s http://127.0.0.1:4100/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"chat","messages":[{"role":"user","content":"Say hi in one word."}]}'
```

Then open the Langfuse UI (the shared instance) → project `metalcrow-gateway` and confirm
the trace, with cost/latency and the serving model.

## Redundancy tests

- **Layer 1:** temporarily set a bad `OPENROUTER_API_KEY_1`, force the OpenRouter path, and
  confirm the retry lands on key 2 (visible in Langfuse).
- **Layer 2:** break the Yandex `api_base`/key, call `chat`, and confirm the fallback to
  `chat-openrouter` — logged as a fallback event.

## Adding a second Yandex key (activate Yandex Layer-1)

Add another block to `config.template.yaml` with the same `model_name: chat` and
`api_key: os.environ/YANDEX_API_KEY_2`, set `YANDEX_API_KEY_2` in `.env`, re-render, restart.

## Notes

- Yandex requires the `x-folder-id` header (set via `extra_headers`); we also send
  `x-data-logging-enabled: false` so Yandex does not retain prompts.
- The Langfuse callback needs the `langfuse` Python SDK. The `litellm:main-stable` image
  bundles it; if the callback errors on startup, build from a small `Dockerfile`
  (`FROM ghcr.io/berriai/litellm:main-stable` + `RUN pip install "langfuse<3"`).
- **Phase 2 (public deploy):** this same stack moves to a fresh immers.cloud VM behind an
  AmnesiaWG (Estonia) VPN with split-tunneling (RU/Yandex direct, OpenRouter via tunnel) and
  an `autumn-lab.uk` subdomain. See the spec §9.
