#!/usr/bin/env bash
# scripts/bridge/run.sh — 启动 agent-bridge HTTP SSE 服务（mac/linux）
#
# 参数透传给 python -m agent_bridge。
# 默认不读取仓库 .env；存量流程可显式传 --legacy-dotenv。
# 默认监听 127.0.0.1:18800。详见 docs/requirements/006-agent-bridge/。
set -euo pipefail
cd "$(dirname "$0")/../.."
if ! command -v uv >/dev/null 2>&1; then
    echo "  [ERROR] 未检测到 uv。先跑 ./scripts/setup/run.sh 初始化环境。" >&2
    exit 1
fi
if ! CODEX_EXECUTABLE="$(uv run python scripts/command-runtime/run.py ensure)"; then
    echo "  [ERROR] 命令运行时准备失败，Bridge 未启动：$CODEX_EXECUTABLE" >&2
    exit 1
fi
if [ -z "$CODEX_EXECUTABLE" ]; then
    echo "  [ERROR] 命令运行时准备成功但未返回可执行文件路径，Bridge 未启动" >&2
    exit 1
fi
export AGENT_FRIEND_CODEX_EXECUTABLE="$CODEX_EXECUTABLE"
exec uv run python -m agent_bridge "$@"
