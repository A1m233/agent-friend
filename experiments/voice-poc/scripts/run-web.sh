#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../rtc-aigc-demo"

echo "→ 启动 RTC AIGC 前端（craco/CRA，端口 3000，会自动打开浏览器）"
echo "  保持此 terminal 开启，停止用 Ctrl+C"
echo "  如浏览器没自动打开，手动访问 http://localhost:3000/"
echo
exec yarn dev
