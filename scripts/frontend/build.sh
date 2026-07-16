#!/usr/bin/env bash
# scripts/frontend/build.sh — 构建生产前端静态资源（mac/linux）
# 详见 scripts/README.md
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_ensure-node.sh"
ensure_node_pnpm
cd "$SCRIPT_DIR/../../frontend"
exec pnpm run build "$@"
