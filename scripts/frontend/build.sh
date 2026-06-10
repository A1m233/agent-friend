#!/usr/bin/env bash
# scripts/frontend/build.sh — 构建桌面端安装包（Tauri build）（mac/linux）
# 详见 scripts/README.md
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_ensure-node.sh"
ensure_node_pnpm
cd "$SCRIPT_DIR/../../frontend"
exec pnpm tauri build "$@"
