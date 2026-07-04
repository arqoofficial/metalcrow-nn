# Phase 2 — Public deploy kit (immers.cloud)

Puts the gateway on a fresh public VM behind TLS.

## Live deployment (2026-07-03)

- **Endpoint:** `https://llm.autumn-lab.uk` (OpenAI-compatible; model `chat` = Yandex primary,
  falls back to `chat-openrouter`). Bearer = `LITELLM_MASTER_KEY` (in the VM's `~/llm-gateway/.env`,
  chmod 600 — retrieve via SSH, never commit).
- **VM:** `195.209.215.26` (immers, Ubuntu 24.04). Fronted by Caddy (auto Let's Encrypt);
  DNS `llm.autumn-lab.uk` A-record is Cloudflare grey-cloud (DNS-only) so Caddy terminates TLS.
- **No VPN/tunnel needed.** Both Yandex and OpenRouter reach direct from this `.215` subnet.
  The original box was on a broken `.210` subnet (blackholed *all* foreign egress incl. Yandex
  Cloud); reprovisioning onto `.215` fixed it. The `wg-*.sh` scripts below are kept for reference
  in case a future host lands on a restricted subnet, but are **not in use**.
- **Langfuse:** disabled on the public node (the shared Langfuse is on the dev VM's loopback,
  unreachable from here — spec §9). `config.public.yaml` = `config.rendered.yaml` with the
  langfuse callbacks stripped.

The AmnesiaWG split/full-tunnel scripts remain below for the restricted-subnet scenario.

## One-shot runbook (once the VM exists)

```bash
# on the fresh immers.cloud VM, as a sudo user:
sudo bash deploy/bootstrap-vm.sh                     # docker + amneziawg-tools + utils

# secrets + VPN
cp /path/to/.env services/llm-gateway/.env           # set LANGFUSE_HOST for the public target (§9)
cp /path/to/estonia.conf services/llm-gateway/deploy/
sudo bash deploy/wg-split-tunnel.sh deploy/estonia.conf   # RU direct, rest via Estonia

# render + launch
cd services/llm-gateway
./render-config.sh
GATEWAY_DOMAIN=gw.autumn-lab.uk docker compose -f deploy/docker-compose.public.yml up -d
```

Then point the `autumn-lab.uk` subdomain's A record at the VM's public IP (Account Manager),
and Caddy auto-provisions the TLS cert on first request.

## Smoke test (public)

```bash
curl -s https://gw.autumn-lab.uk/health/liveliness
curl -s https://gw.autumn-lab.uk/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model":"chat","messages":[{"role":"user","content":"hi"}]}'
```

## Decisions still open (coordinate)

- **RU-CIDR source** for the split-tunnel — `wg-split-tunnel.sh` defaults to ipdeny's RU zone;
  confirm the canonical list/approach with **Account Manager**.
- **Langfuse target on the public VM** (spec §9): reach back to the shared instance (needs
  secure exposure) vs. run an own instance. Set `LANGFUSE_HOST` in `.env` accordingly, or drop
  the langfuse callbacks in `config.template.yaml` if tracing isn't wanted on the public node.
- **Package names** for AmnesiaWG vary by distro — `bootstrap-vm.sh` is best-effort; verify
  against amneziawg.org for the VM's OS.
