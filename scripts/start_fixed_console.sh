#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${PROJECT_ROOT}/lelamp_runtime"
DEFAULTS_FILE="${PROJECT_ROOT}/config/runtime-defaults.env"

cd "${RUNTIME_DIR}"

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

export OPENCLAW_ENABLE_HARDWARE="${OPENCLAW_ENABLE_HARDWARE:-1}"
export LELAMP_PORT="${LELAMP_PORT:-/dev/ttyACM0}"
export LELAMP_ID="${LELAMP_ID:-lelamp}"
export LELAMP_WEB_TOKEN="${LELAMP_WEB_TOKEN:-${LELAMP_DEFAULT_WEB_TOKEN}}"
TAILSCALE_IP="${LELAMP_TAILSCALE_IP:-}"
if [[ -z "${TAILSCALE_IP}" ]] && command -v tailscale >/dev/null 2>&1; then
  TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "${TAILSCALE_IP}" ]] && command -v ip >/dev/null 2>&1; then
  TAILSCALE_IP="$(ip -4 addr show tailscale0 2>/dev/null | awk '/inet / { split($2, a, "/"); print a[1]; exit }')"
fi
LAN_IP="${LELAMP_LAN_IP:-}"
if [[ -z "${LAN_IP}" ]] && command -v hostname >/dev/null 2>&1; then
  LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)/ { print; exit }')"
fi
if [[ -z "${LAN_IP}" ]] && command -v ip >/dev/null 2>&1; then
  LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{ for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit } }')"
fi
LAN_IP="${LAN_IP:-${TAILSCALE_IP:-127.0.0.1}}"
export LELAMP_PUBLIC_URL="${LELAMP_PUBLIC_URL:-http://${LAN_IP}:8790}"
export LELAMP_CAMERA_BROWSER_URL="${LELAMP_CAMERA_BROWSER_URL:-http://${LAN_IP}:8788}"
export LELAMP_CAMERA_INDEX="${LELAMP_CAMERA_INDEX:-1}"
export LELAMP_CAMERA_STREAM_INDEX="${LELAMP_CAMERA_STREAM_INDEX:-1}"
export LELAMP_CAMERA_STREAM_PORT="${LELAMP_CAMERA_STREAM_PORT:-8788}"
export LELAMP_STARTUP_HOME="${LELAMP_STARTUP_HOME:-0}"
export OPENCLAW_MIC_DEVICE="${OPENCLAW_MIC_DEVICE:-plughw:2,0}"
export OPENCLAW_SPEAKER_DEVICE="${OPENCLAW_SPEAKER_DEVICE:-hw:3,0}"
export LD_LIBRARY_PATH="${HOME}/.local/sysroot/root/usr/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"

exec "${RUNTIME_DIR}/.venv/bin/python3" -u openclaw_cli.py web-console \
  --host 0.0.0.0 \
  --port 8790 \
  --projection-preview-port 8765
