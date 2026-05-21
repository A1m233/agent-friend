#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../rtc-aigc-demo/Server"

echo "→ 启动 RTC AIGC Server（koa，端口 3001）"
echo "  保持此 terminal 开启，停止用 Ctrl+C"
echo
exec yarn dev
