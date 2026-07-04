# LiteLLM + Langfuse Gateway — Design Spec

**Date:** 2026-07-03
**Branch:** `feature/llm-gateway` (forked from `osn-pre-main` @ e7d948e; **never touches `main`**)
**Author:** Litellm-gw session (Claude Code, `workdirs/metalcrow`)
**Status:** Draft for review

## 1. Goal

Stand up a **LiteLLM proxy** that gives every agent/service in the fleet a single,
OpenAI-compatible endpoint instead of calling cloud LLM providers directly, with
**two layers of redundancy**:

1. **Intra-provider key failover** — if one API key hits a rate limit (HTTP 429),
   automatically retry with a backup key for the *same* provider.
2. **Cross-provider failover** — if a whole provider fails, fall back to a *different*
   provider entirely.

All calls are logged/traced to a **shared, self-hosted Langfuse** (cost, latency,
which key/provider served the request, and whether a fallback occurred), on our own
infra — never Langfuse Cloud.

Configuration is a **single YAML** listing models/keys and the fallback order. The
proxy runs as a **containerized** local server.

## 2. Constraints & non-goals

- **Never modify `main`** (metalcrow) or the `cosmetic-agent` repo's `main`.
- **Full isolation** from the concurrent `feature/extraction-benchmark` session: all
  work happens in a sibling worktree; no edits under `term_dictionary/`.
- **No secret is ever committed.** Keys live only in a git-ignored `.env` sourced from
  the out-of-repo `../safe/` directory.
- Not building a custom router or a UI — LiteLLM's built-in router + Langfuse UI suffice.

## 3. Placement & runtime

- Self-contained directory: **`services/llm-gateway/`**, containing:
  - `docker-compose.yml` — the LiteLLM service
  - `config.yaml` — the model list, router settings, fallbacks
  - `.env.example` — documented variable names, **no values**
  - `.env` — **git-ignored**; populated from `../safe/` (see §7)
  - `README.md` — run instructions + fault-injection test recipe
- **Containerized** (own compose file, not wired into the app's root `compose.yml`),
  so it lifts onto the Phase-2 public VM verbatim.
- **Port:** LiteLLM binds `127.0.0.1:4100` (host `:4000` is the existing Langfuse server).
- **Admin auth:** a `LITELLM_MASTER_KEY` (in `.env`) guards proxy admin/key endpoints.

## 4. Providers & credentials

| Role | Provider | Model(s) | Creds (in `../safe/`) |
|---|---|---|---|
| **Primary** | Yandex Foundation Models (YandexGPT) | `gpt://<folder>/yandexgpt/latest` (+ `…/yandexgpt-lite`, `…/llama` as needed) | `nornickel_hack_yandex_api_key` (Api-Key), `nornickel_hack_yandex_folder_id` |
| **Fallback** | OpenRouter | operator-chosen model (e.g. an OSS or GPT-class model) | `nornickel_hack_or_key`, `or_key_0` (**two keys → real intra-provider failover**) |

**Key-count reality (2026-07-03):**
- Yandex: **1** key → intra-provider (Layer-1) failover is *not yet active*; it activates
  the instant a 2nd Yandex key is added (a one-block edit to `config.yaml`).
- OpenRouter: **2** keys → Layer-1 failover active.
- Layer-2 (Yandex → OpenRouter) active now.

## 5. Config schema (single YAML — both redundancy layers)

LiteLLM's router provides both layers natively:

- **Layer 1:** multiple deployments sharing one `model_name` form a failover/round-robin
  group; a 429'd key is cooled down (`cooldown_time`) and a sibling key is retried.
- **Layer 2:** `router_settings.fallbacks` maps a model group to an ordered list of other
  groups, tried when the primary group fails.

```yaml
model_list:
  # ---- Layer 1: same model_name, different keys = intra-provider failover ----
  - model_name: chat                                   # what agents call
    litellm_params:
      model: openai/gpt://${YANDEX_FOLDER_ID}/yandexgpt/latest  # Yandex OpenAI-compat endpoint
      api_base: https://llm.api.cloud.yandex.net/v1
      api_key: os.environ/YANDEX_API_KEY_1
      extra_headers:
        x-folder-id: "${YANDEX_FOLDER_ID}"             # REQUIRED by Yandex
        x-data-logging-enabled: "false"                # privacy: don't let Yandex retain prompts
    model_info: { id: yandex-1, provider: yandex }
  # (2nd Yandex key → duplicate this block with model_name: chat + YANDEX_API_KEY_2)

  # ---- Fallback provider group (also Layer-1 across its 2 keys) ----
  - model_name: chat-openrouter
    litellm_params: { model: openrouter/<MODEL>, api_key: os.environ/OPENROUTER_KEY_1 }
    model_info: { id: or-1, provider: openrouter }
  - model_name: chat-openrouter
    litellm_params: { model: openrouter/<MODEL>, api_key: os.environ/OPENROUTER_KEY_2 }
    model_info: { id: or-2, provider: openrouter }

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  cooldown_time: 30           # seconds a rate-limited deployment is parked
  allowed_fails: 2
  fallbacks:
    - { "chat": ["chat-openrouter"] }   # Layer 2: provider down → other provider

litellm_settings:
  success_callback: ["langfuse"]
  failure_callback: ["langfuse"]        # fallbacks & failures logged too
  drop_params: true                     # tolerate provider param differences

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

Agents always call the single model name `chat`; the router handles rotation and fallback
transparently.

## 6. Yandex "non-standard API" handling (confirmed against docs)

Yandex is not a first-class LiteLLM provider, but its **OpenAI-compatible endpoint** works
through LiteLLM's `openai/` custom provider. Per the Yandex Cloud docs
(<https://yandex.cloud/en/docs/ai-studio/concepts/openai-compatibility>):

- **Endpoint:** `https://llm.api.cloud.yandex.net/v1` (`api_base`).
- **Auth:** `Authorization: Api-Key <key>` (LiteLLM sets this from `api_key`).
- **Required extra header:** `x-folder-id: <folder-id>`.
- **Optional privacy header:** `x-data-logging-enabled: false` (stop Yandex retaining
  prompts) — **set to false** for the hackathon corpus.
- **Model URI:** `gpt://<folder-id>/yandexgpt/latest`.

LiteLLM injects the extra headers via `litellm_params.extra_headers` (confirmed in LiteLLM
docs). The `<folder-id>` appears in *both* the model URI and the header.

**Secret-free config:** because the folder id would otherwise be baked into the committed
`config.yaml`, the committed file is a **template** (`config.template.yaml`) using
`${YANDEX_FOLDER_ID}`; the container entrypoint runs `envsubst` on the `${...}` placeholders
to produce the runtime config. LiteLLM's own `os.environ/VAR` references (used for API keys)
contain no `${}` and pass through `envsubst` untouched.

**R1 spike (do first):** one live Yandex completion through the proxy returning a result
gates the rest of the build. If the OpenAI-compat path can't carry the `gpt://` URI, fall
back to a thin adapter (`YandexGPT_to_OpenAI`-style shim) — but docs indicate the direct
path works.

## 7. Langfuse wiring (shared instance) & secrets

- **Shared Langfuse — already provisioned** by `chemcrow-deploy` on the Cosmetica Langfuse
  instance: org **"Metalcrow"**, project **`metalcrow-gateway`** (id `cmr4w9kdw0007o3079epehty4`).
  Keys are **project-scoped** — they see only metalcrow traces, not Cosmetica's.
- Creds staged at `../safe/langfuse-metalcrow.env`: `LANGFUSE_PUBLIC_KEY` (`pk-…`),
  `LANGFUSE_SECRET_KEY` (`sk-…`), `LANGFUSE_HOST`.
- **Reachability:** the Langfuse host port is `127.0.0.1:4000` (loopback), so a container
  on another docker network can't reach it via the bridge gateway. Our compose **attaches
  the existing `cosmetic-agent_default` network as external** and points
  `LANGFUSE_HOST=http://langfuse-server:3000` (internal service name).
- **Secrets flow:** `services/llm-gateway/.env` (git-ignored) is assembled from the
  `../safe/` files (Yandex key + folder, both OpenRouter keys, Langfuse trio, master key).
  Repo commits `config.yaml`, `docker-compose.yml`, `.env.example`, `README.md` only.
- **Committed in both repos:** the *client wiring* (Langfuse host + project name +
  env-var names, no secrets) is documented in the metalcrow spec/README; the
  `cosmetic-agent` side already owns the server. This satisfies "shared, committed in both."

## 8. Testing

- **Health:** `config.yaml` loads; `GET /health/liveliness` green.
- **Live call:** a `chat` completion returns *and* appears in Langfuse under
  `metalcrow-gateway` only, with cost + latency + model metadata.
- **Layer-1:** inject a bad OpenRouter key → observe cooldown + retry on the sibling key
  (verify in Langfuse the second key served the retry).
- **Layer-2:** force Yandex failure (bad base/key) → observe fallback to `chat-openrouter`,
  logged as a fallback event.
- **Trace metadata:** confirm session id / tags pass through to Langfuse.

## 9. Phase 2 — public team service (deferred, "at the very end")

Ordered, done only after Phase 1 works locally:

1. **Provision a fresh immers.cloud VM** (public IP) — with help from **nuextract3 bot**
   for the immers.cloud API. Note: immers.cloud is a **Russian** provider.
2. **VPN (AmnesiaWG, Estonia exit):** deploy `../safe/estonia.conf` on the VM using
   `amneziawg-tools` (`awg-quick`) — **not** vanilla `wg-quick` (config uses AmnesiaWG
   obfuscation params `Jc/Jmin/Jmax/S1–S4/H1–H4/i1`). Peer `95.156.206.11:41932`.
3. **Split tunneling** (contact **Account Manager** to set up): **RU-destined traffic goes
   direct** (outside the tunnel) — notably **Yandex** (`llm.api.cloud.yandex.net` → RU IPs),
   which works natively from the RU IP; **foreign traffic (OpenRouter) exits via Estonia**
   (it is otherwise blocked from a Russian IP). Implemented via policy routing over a RU
   CIDR set, replacing the config's blunt `AllowedIPs = 0.0.0.0/0` default.
4. **Deploy** the same `services/llm-gateway/` compose on the VM.
5. **DNS:** request an **`autumn-lab.uk` subdomain** from **Account Manager**, front the
   proxy with TLS.
6. **Langfuse target on the public VM** (own instance vs. reach back to the shared one):
   decided at Phase 2, not now.

## 10. Coordination log (A2A bus)

- Announced the workstream + isolation approach: topic `metalcrow-litellm-gw` (msg 1689).
- Opened shared-Langfuse negotiation with `chemcrow-deploy`: topic `shared-langfuse`
  (msg 1694) — creds were pre-provisioned in `../safe/langfuse-metalcrow.env`.
- Phase-2 contacts pending: **nuextract3 bot** (immers.cloud API), **Account Manager**
  (subdomain + split-tunnel).

## 11. Open questions / risks

- **R1 (highest):** Yandex via LiteLLM OpenAI-compat — prove with a spike before building
  the rest (§6).
- **Q1:** Which OpenRouter model is the fallback? (operator to pick; placeholder `<MODEL>`.)
- **Q2:** Add a 2nd Yandex key to activate Yandex Layer-1? (optional; config supports it.)
- **Q3:** Confirm `chemcrow-deploy` is OK long-term with the shared-project arrangement
  (bus reply pending; creds already staged, so low risk).
