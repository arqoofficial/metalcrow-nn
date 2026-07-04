# 🚀 New: shared LLM Gateway + tracing (on our own infra)

We now have a **shared, self-hosted LLM gateway** — one OpenAI-compatible endpoint for the whole
team, with automatic provider failover and full call tracing. Nothing leaves our infra.

## Endpoints
- **Gateway (API):** `https://llm.autumn-lab.uk` — OpenAI-compatible (`/v1/...`)
- **Langfuse (tracing UI):** `https://langfuse.autumn-lab.uk`

## Use it
Point any OpenAI client at the gateway — just swap the base URL and key:

```python
from openai import OpenAI
client = OpenAI(base_url="https://llm.autumn-lab.uk/v1", api_key="<gateway-key>")
r = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[{"role": "user", "content": "hello"}],
)
```

```bash
curl https://llm.autumn-lab.uk/v1/chat/completions \
  -H "Authorization: Bearer <gateway-key>" -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-oss-120b","messages":[{"role":"user","content":"hi"}]}'
```

**Gateway key:** ping me — it's kept out of git.

## Models (call by these names — standard OpenRouter ids)
| Model | |
|---|---|
| `openai/gpt-oss-120b` | OpenAI open-weight, strong reasoning |
| `openai/gpt-oss-20b`  | smaller / faster |
| `deepseek/deepseek-v4-flash` | |
| `deepseek/deepseek-v3.2` | |
| `qwen/qwen3-235b-a22b` | large MoE |

`GET /v1/models` lists them. Note: gpt-oss / deepseek are **reasoning models** — give them a
generous `max_tokens`, or the hidden reasoning eats your budget and `content` comes back empty.

## What you get for free
- **Redundancy** — each model runs **Yandex (primary) → OpenRouter (fallback)** automatically:
  a rate-limited key is retried on a backup key; a provider outage fails over to the other
  provider. You just call one model name.
- **Tracing** — every call is logged to our **self-hosted Langfuse** (cost, latency, tokens, which
  provider served it): `https://langfuse.autumn-lab.uk` (login on request).
- **On our infra** — runs on our own VM; prompts & responses stay on our Langfuse, not a vendor cloud.

## Good to know
- Yandex-only models (YandexGPT, Alice) aren't exposed — they have no OpenRouter name. Ask if you need one.
- Need another model, a key, or Langfuse access? → ping me.
