#!/usr/bin/env bash
# Regenerate uv.lock (CPU torch) and uv.lock.gpu (CUDA torch from PyPI).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYPROJECT="${ROOT_DIR}/pyproject.toml"
TMP_PYPROJECT="$(mktemp)"

cleanup() {
  rm -f "${TMP_PYPROJECT}"
}
trap cleanup EXIT

echo "==> uv.lock (CPU, pytorch-cpu index)"
uv lock

echo "==> uv.lock.gpu (CUDA torch from PyPI)"
python3 - <<'PY' "${PYPROJECT}" "${TMP_PYPROJECT}"
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")
# Drop CPU-only torch source overrides and the explicit pytorch-cpu index.
src = re.sub(
    r"\n# Force CPU-only torch builds.*?explicit = true\n",
    "\n",
    src,
    count=1,
    flags=re.DOTALL,
)
Path(sys.argv[2]).write_text(src, encoding="utf-8")
PY

BACKUP_PYPROJECT="${ROOT_DIR}/pyproject.toml.cpu.bak"
mv "${PYPROJECT}" "${BACKUP_PYPROJECT}"
cp "${TMP_PYPROJECT}" "${PYPROJECT}"
uv lock
mv uv.lock uv.lock.gpu
mv "${BACKUP_PYPROJECT}" "${PYPROJECT}"
uv lock

echo "Done: uv.lock (CPU), uv.lock.gpu (CUDA)"
