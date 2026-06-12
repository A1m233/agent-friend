#!/usr/bin/env bash
# scripts/dev-fire-source/run.sh — 立即触发 bridge 上指定 EventSource 的 fire_now（mac/linux）
#
# 用法：./scripts/dev-fire-source/run.sh <source_name> [bridge_url]
# 例：  ./scripts/dev-fire-source/run.sh cron:bedtime
#       ./scripts/dev-fire-source/run.sh idle_reflection http://127.0.0.1:18800
#
# 注意：仅在 bridge 配置 AGENT_BRIDGE_DEV_MODE=true 时挂载该端点；生产环境永不开。
# 详见 docs/requirements/014-engine-main-loop-and-bridge-push/。
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ "$#" -lt 1 ]; then
    echo "用法：$0 <source_name> [bridge_url]" >&2
    echo "   source_name: 如 cron:bedtime / idle_reflection" >&2
    exit 2
fi

SOURCE_NAME="$1"
URL="${2:-http://127.0.0.1:18800}"

exec curl -fsS -X POST "$URL/dev/fire-source?source_name=$(printf %s "$SOURCE_NAME" | python -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read()))')"
