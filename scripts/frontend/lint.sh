#!/usr/bin/env bash
# scripts/frontend/lint.sh — 前端 lint（ESLint + 颜色门禁）+ 类型检查（tsc）（mac/linux）
# 详见 scripts/README.md
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_ensure-node.sh"
ensure_node_pnpm
cd "$SCRIPT_DIR/../../frontend"
echo "===> eslint"
pnpm run lint
echo "===> color-guard"
pnpm run lint:colors
echo "===> ui-provenance-guard"
pnpm run lint:ui-provenance
echo "===> design-token-guard"
pnpm run lint:tokens
echo "===> typecheck"
pnpm run typecheck
