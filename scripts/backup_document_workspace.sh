#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_DIR="${OPENCLAW_WORKSPACE:-${PROJECT_ROOT}/lelamp_runtime/workspace}"
OUTPUT_DIR="${1:-${PROJECT_ROOT}/backups}"
DOCUMENT_DIR="${WORKSPACE_DIR}/.documents"

if [[ ! -d "${DOCUMENT_DIR}" ]]; then
  echo "未找到文档数据目录：${DOCUMENT_DIR}" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="${OUTPUT_DIR}/lelamp-documents-${STAMP}.tar.gz"
tar -C "${WORKSPACE_DIR}" -czf "${ARCHIVE}" .documents
sha256sum "${ARCHIVE}" > "${ARCHIVE}.sha256"
printf '%s\n' "${ARCHIVE}"
