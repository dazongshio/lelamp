#!/usr/bin/env bash
set -euo pipefail

cd /home/dazis/LeLamp/lelamp_runtime

export OPENCLAW_ENABLE_HARDWARE="${OPENCLAW_ENABLE_HARDWARE:-1}"
export LELAMP_PORT="${LELAMP_PORT:-/dev/ttyACM0}"
export LELAMP_ID="${LELAMP_ID:-lelamp}"
export LELAMP_WEB_TOKEN="${LELAMP_WEB_TOKEN:-lelamp-local-console}"
export LELAMP_PUBLIC_URL="${LELAMP_PUBLIC_URL:-http://100.88.73.34:8790}"
export LELAMP_CAMERA_BROWSER_URL="${LELAMP_CAMERA_BROWSER_URL:-http://100.88.73.34:8788}"
export LELAMP_CAMERA_INDEX="${LELAMP_CAMERA_INDEX:-1}"
export LELAMP_CAMERA_STREAM_INDEX="${LELAMP_CAMERA_STREAM_INDEX:-1}"
export LELAMP_CAMERA_STREAM_PORT="${LELAMP_CAMERA_STREAM_PORT:-8788}"
export LELAMP_STARTUP_HOME="${LELAMP_STARTUP_HOME:-1}"
export OPENCLAW_MIC_DEVICE="${OPENCLAW_MIC_DEVICE:-plughw:2,0}"
export OPENCLAW_SPEAKER_DEVICE="${OPENCLAW_SPEAKER_DEVICE:-hw:3,0}"

exec /home/dazis/LeLamp/lelamp_runtime/.venv/bin/python3 -u openclaw_cli.py web-console \
  --host 0.0.0.0 \
  --port 8790 \
  --projection-preview-port 8765
