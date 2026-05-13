#!/usr/bin/env bash
# scripts/cli/run.sh — 启动 agent-friend CLI 调试 UI（mac/linux）
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m tools.cli "$@"
