#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${PROJECT_DIR}/lelamp_runtime"
DEFAULTS_FILE="${PROJECT_DIR}/config/runtime-defaults.env"

if [[ ! -f "${DEFAULTS_FILE}" ]]; then
  echo "Missing shared runtime defaults: ${DEFAULTS_FILE}" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${DEFAULTS_FILE}"
set +a

if [[ -f "${RUNTIME_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${RUNTIME_DIR}/.env"
  set +a
fi

export LELAMP_WEB_TOKEN="${LELAMP_WEB_TOKEN:-${LELAMP_DEFAULT_WEB_TOKEN}}"
export OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-${RUNTIME_DIR}/workspace}"
export LELAMP_COLLAB_HOST="${LELAMP_COLLAB_HOST:-0.0.0.0}"
export LELAMP_COLLAB_PORT="${LELAMP_COLLAB_PORT:-8791}"

exec /usr/bin/node "${PROJECT_DIR}/scripts/collaboration-server.mjs"
