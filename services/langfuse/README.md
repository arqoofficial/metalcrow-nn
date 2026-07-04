# Self-hosted Langfuse (`services/langfuse`)

The gateway's **own** tracing backend — a self-contained Langfuse v3 stack, co-located on the
gateway VM alongside LiteLLM (so the public gateway has cost/latency/trace observability without
depending on another team's instance). Spec §9.

Based on the proven Cosmetica deploy (thanks chemcrow-deploy): `minio/minio`, the
`wget`+`$(hostname)` healthcheck, and the ClickHouse macros/zookeeper XMLs are all folded in.

## Requirements

- **~8 GB RAM** — ClickHouse alone wants ~2 GB. The gateway VM was resized `cpu.2.4.40 → cpu.4.8.60`
  (4 vCPU / 8 GB / 60 GB) for this. Do **not** run it on the 4 GB flavor.
- DNS `langfuse.autumn-lab.uk` → the gateway VM (Cloudflare grey-cloud, already set by Account Manager).

## Deploy

```bash
cd services/langfuse
cp .env.example .env
#   edit .env: set LANGFUSE_DOMAIN + LANGFUSE_INIT_USER_PASSWORD
./gen-secrets.sh                 # fills crypto secrets + the gateway's pk-lf-/sk-lf- project keys
docker compose up -d             # ~90s for first-boot migrations
docker compose logs -f langfuse-server   # watch until "Ready"
```

The gateway's Caddy (in `../llm-gateway/deploy/`) already has a `langfuse.autumn-lab.uk` site block
(→ `host.docker.internal:3000`), so the UI comes up at **`https://langfuse.autumn-lab.uk`** with
auto-TLS. Log in with `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`.

## Wire the gateway to it

In `../llm-gateway/.env` on the VM, set the keys `gen-secrets.sh` produced and re-enable tracing:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...          # from services/langfuse/.env
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://langfuse-server:3000   # reached over the shared `langfuse_default` network
```

The gateway's `docker-compose.public.yml` already (a) mounts the full `config.rendered.yaml`
(keeps `success_callback: ["langfuse"]`) and (b) joins the external `langfuse_default` network,
so `litellm` and `caddy` resolve `langfuse-server` by name. Bring the Langfuse stack up **first**
(it creates that network), then `docker compose -f deploy/docker-compose.public.yml up -d`. Every
call then traces to project **`metalcrow-gateway`** (org **Metalcrow**), with cost + latency.

## Notes / gotchas (from the Cosmetica deploy)

- `LANGFUSE_HOST` inside a container must be a reachable address, **not** `localhost`.
- The SDK batches — short-lived callers must `flush()` before exit. LiteLLM's callback handles this.
- Media (trace attachments) upload to the internal MinIO; browser download of media needs a
  reachable S3 endpoint — fine for LLM tracing (tokens/latency/cost), revisit if you attach media.
- `LANGFUSE_INIT_*` seed the org/project/keys only on a **fresh** DB; ignored once data exists.
