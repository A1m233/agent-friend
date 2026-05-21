#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "→ 火山 RTC AIGC 连通性测试（StartVoiceChat → StopVoiceChat）"
echo "  会真创建 RTC 房间 + TTS 一次 WelcomeMessage（估算 ≤ ¥0.1）"
echo

export NODE_PATH="$SCRIPT_DIR/../rtc-aigc-demo/Server/node_modules"
exec node "$SCRIPT_DIR/connectivity-test.js"
