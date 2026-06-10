#!/usr/bin/env bash
# scripts/frontend/install.sh — 安装前端依赖（mac/linux）
# 详见 scripts/README.md
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_ensure-node.sh"

echo "==> 检查 node / pnpm"
ensure_node_pnpm
echo "  node $(node -v) / pnpm $(pnpm -v)"

echo
echo "==> 安装前端依赖（pnpm install @ frontend/）"
cd "$SCRIPT_DIR/../../frontend"
exec pnpm install
