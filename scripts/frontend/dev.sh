#!/usr/bin/env bash
# scripts/frontend/dev.sh — 启动桌面端开发（Tauri dev，含双窗口）（mac/linux）
# 详见 scripts/README.md
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/_ensure-node.sh"
ensure_node_pnpm
cd "$SCRIPT_DIR/../../frontend"
exec pnpm tauri dev "$@"
