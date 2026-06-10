#!/usr/bin/env bash
# scripts/frontend/test.sh — 前端单测（vitest）（mac/linux）
# 详见 scripts/README.md。参数透传给 vitest：./scripts/frontend/test.sh -t foo
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_ensure-node.sh"
ensure_node_pnpm
cd "$SCRIPT_DIR/../../frontend"
echo "===> vitest"
pnpm run test "$@"
