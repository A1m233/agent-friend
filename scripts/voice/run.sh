#!/usr/bin/env bash
# scripts/voice/run.sh — 启动 voice-bridge 控制平面 + LLM 入站代理（mac/linux）
#
# 参数透传给 python -m voice_bridge。
# 默认不读取仓库 .env；存量流程可显式传 --legacy-dotenv。
# 默认监听 127.0.0.1:18900。详见 docs/requirements/007-voice-call/。
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m voice_bridge "$@"
