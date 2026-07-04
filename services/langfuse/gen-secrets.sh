#!/usr/bin/env bash
# Fill the empty secret values in .env (idempotent — only sets blanks, never overwrites).
# Generates: internal passwords, NEXTAUTH_SECRET, SALT, ENCRYPTION_KEY (256-bit hex),
# and the gateway's project keys (pk-lf-.../sk-lf-...) seeded into Langfuse on first boot.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "copy .env.example -> .env first"; exit 1; }

setblank() {  # setblank KEY VALUE  — only if KEY= is currently empty (or whitespace-only)
  local k="$1" v="$2"
  if grep -qE "^${k}=[[:space:]]*$" .env; then
    sed -i "s|^${k}=[[:space:]]*$|${k}=${v}|" .env
    echo "  set ${k}"
  else
    echo "  ${k} already set — kept"
  fi
}

uuid() { cat /proc/sys/kernel/random/uuid; }

setblank LANGFUSE_DB_PASSWORD        "$(openssl rand -hex 16)"
setblank LANGFUSE_CLICKHOUSE_PASSWORD "$(openssl rand -hex 16)"
setblank LANGFUSE_MINIO_PASSWORD     "$(openssl rand -hex 16)"
setblank NEXTAUTH_SECRET             "$(openssl rand -base64 32)"
setblank SALT                        "$(openssl rand -base64 32)"
setblank ENCRYPTION_KEY              "$(openssl rand -hex 32)"     # 64 hex chars = 256-bit
setblank LANGFUSE_INIT_USER_PASSWORD "$(openssl rand -hex 12)"
setblank LANGFUSE_PUBLIC_KEY         "pk-lf-$(uuid)"
setblank LANGFUSE_SECRET_KEY         "sk-lf-$(uuid)"

chmod 600 .env
echo "done. Project keys for the gateway:"
grep -E '^LANGFUSE_(PUBLIC|SECRET)_KEY=' .env
