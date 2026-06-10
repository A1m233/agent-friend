#!/usr/bin/env bash
# scripts/frontend/web.sh — 仅启动前端 web dev server（浏览器调试，不编译 Rust）（mac/linux）
# 详见 scripts/README.md
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_ensure-node.sh"
ensure_node_pnpm
cd "$SCRIPT_DIR/../../frontend"
exec pnpm dev "$@"
