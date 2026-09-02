#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "用法：$0 <备份文件.tar.gz> <目标工作区>" >&2
  exit 2
fi

ARCHIVE="$(realpath "$1")"
TARGET_WORKSPACE="$(realpath -m "$2")"
if [[ ! -f "${ARCHIVE}" ]]; then
  echo "备份文件不存在：${ARCHIVE}" >&2
  exit 1
fi
if ! tar -tzf "${ARCHIVE}" | awk '
  $0 !~ /^\.documents(\/|$)/ { bad=1 }
  $0 ~ /(^|\/)\.\.($|\/)/ || $0 ~ /^\// { bad=1 }
  END { exit bad ? 1 : 0 }
'; then
  echo "备份包包含不安全或非文档路径，已拒绝恢复。" >&2
  exit 1
fi

mkdir -p "${TARGET_WORKSPACE}"
if [[ -e "${TARGET_WORKSPACE}/.documents" ]]; then
  echo "目标已有 .documents，请使用空目录恢复，避免覆盖现有数据。" >&2
  exit 1
fi
tar -C "${TARGET_WORKSPACE}" -xzf "${ARCHIVE}"
printf '%s\n' "${TARGET_WORKSPACE}/.documents"
