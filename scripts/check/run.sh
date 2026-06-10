#!/usr/bin/env bash
# scripts/check/run.sh — 合入 main 前的一键全绿检查（mac/linux）
# 串联：lint（ruff check + format --check）→ typecheck（mypy strict）→ test（pytest）
# 任意一步失败立即退出。pytest 参数可透传：./scripts/check/run.sh -k foo
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

echo "===> [1/4] lint"
"$here/../lint/run.sh"
echo
echo "===> [2/4] typecheck"
"$here/../typecheck/run.sh"
echo
echo "===> [3/4] test"
"$here/../test/run.sh" "$@"
echo
echo "===> [4/4] frontend lint + test"
if command -v node >/dev/null 2>&1 && [ -d "$here/../../frontend/node_modules" ]; then
    "$here/../frontend/lint.sh"
    "$here/../frontend/test.sh"
else
    echo "  [skip] 前端工具链/依赖未就绪（无 node 或未跑 frontend/install），跳过前端 lint + test"
fi
echo
echo "===> check 全部通过"
