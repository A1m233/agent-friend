#!/usr/bin/env bash
# scripts/check/run.sh — 合入 main 前的一键全绿检查（mac/linux）
# 串联：lint（ruff check + format --check）→ typecheck（mypy strict）→ test（pytest）
# 任意一步失败立即退出。pytest 参数可透传：./scripts/check/run.sh -k foo
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

echo "===> [1/3] lint"
"$here/../lint/run.sh"
echo
echo "===> [2/3] typecheck"
"$here/../typecheck/run.sh"
echo
echo "===> [3/3] test"
"$here/../test/run.sh" "$@"
echo
echo "===> check 全部通过"
