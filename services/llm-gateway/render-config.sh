#!/usr/bin/env bash
# Render config.template.yaml -> config.rendered.yaml, substituting ONLY ${YANDEX_FOLDER_ID}.
# LiteLLM's own os.environ/ key refs contain no `$`, so envsubst leaves them untouched.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "ERROR: .env not found. Copy .env.example -> .env and fill from ../safe/ (see README)." >&2; exit 1; }

# Load .env into the environment for substitution.
set -a; . ./.env; set +a

: "${YANDEX_FOLDER_ID:?YANDEX_FOLDER_ID must be set in .env}"

if command -v envsubst >/dev/null 2>&1; then
  envsubst '${YANDEX_FOLDER_ID}' < config.template.yaml > config.rendered.yaml
else
  python3 - "$YANDEX_FOLDER_ID" <<'PY'
import re, sys
folder = sys.argv[1]
src = open("config.template.yaml").read()
open("config.rendered.yaml", "w").write(src.replace("${YANDEX_FOLDER_ID}", folder))
PY
fi
echo "rendered -> config.rendered.yaml"
