#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu VM (immers.cloud) to run the metalcrow LiteLLM gateway publicly.
# Run once, as a sudo-capable user:  sudo bash bootstrap-vm.sh
# Idempotent where practical. After this: place .env + estonia.conf, run wg-split-tunnel.sh,
# render config, then `docker compose -f deploy/docker-compose.public.yml up -d`.
set -euo pipefail

echo "== 1. Docker + compose plugin =="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || apt-get install -y docker-compose-plugin

echo "== 2. AmnesiaWG (awg-quick) — obfuscated WireGuard =="
# NOTE: package availability varies by distro/release; consult https://docs.amnezia.org / amneziawg.org
if ! command -v awg-quick >/dev/null 2>&1; then
  apt-get update
  add-apt-repository -y ppa:amnezia/ppa 2>/dev/null || true
  apt-get update || true
  apt-get install -y amneziawg-tools amneziawg-dkms 2>/dev/null \
    || apt-get install -y amneziawg 2>/dev/null \
    || echo "WARN: install amneziawg-tools manually (see amneziawg.org) — awg-quick required for the VPN step"
fi

echo "== 3. Utilities (envsubst for config render, curl) =="
command -v envsubst >/dev/null 2>&1 || apt-get install -y gettext-base
command -v curl >/dev/null 2>&1 || apt-get install -y curl

echo "== done =="
echo "Next:"
echo "  1) copy services/llm-gateway/.env here (set LANGFUSE_HOST for the public target — see spec §9)"
echo "  2) copy estonia.conf here; run: sudo bash deploy/wg-split-tunnel.sh estonia.conf"
echo "  3) ./render-config.sh"
echo "  4) GATEWAY_DOMAIN=<your.subdomain> docker compose -f deploy/docker-compose.public.yml up -d"
