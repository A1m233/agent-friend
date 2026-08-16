#!/usr/bin/env bash
# scripts/bridge/run.sh — 启动 agent-bridge HTTP SSE 服务（mac/linux）
#
# 参数透传给 python -m agent_bridge。
# 默认不读取仓库 .env；存量流程可显式传 --legacy-dotenv。
# 默认监听 127.0.0.1:18800。详见 docs/requirements/006-agent-bridge/。
set -euo pipefail
cd "$(dirname "$0")/../.."
if CODEX_EXECUTABLE="$(uv run python scripts/command-runtime/run.py path 2>/dev/null)"; then
    export AGENT_FRIEND_CODEX_EXECUTABLE="$CODEX_EXECUTABLE"
else
    unset AGENT_FRIEND_CODEX_EXECUTABLE || true
fi
exec uv run python -m agent_bridge "$@"
