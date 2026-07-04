#!/usr/bin/env bash
# Refresh vendored spaCy/scispaCy model tarballs (run from a network that can
# reach Allen AI S3). Docker builds install from models/ — no runtime fetch.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${ROOT}/models"
cd "${MODELS_DIR}"

EN_CORE_SCI_SM_URL="https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz"
EN_CORE_SCI_SM_FILE="en_core_sci_sm-0.5.4.tar.gz"

curl -fsSL -o "${EN_CORE_SCI_SM_FILE}" "${EN_CORE_SCI_SM_URL}"
shasum -a 256 "${EN_CORE_SCI_SM_FILE}" | tee SHA256SUMS

echo "Updated ${MODELS_DIR}/${EN_CORE_SCI_SM_FILE}"
