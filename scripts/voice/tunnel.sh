#!/usr/bin/env bash
# scripts/voice/tunnel.sh — 启动 cloudflared 公网穿透样例（mac/linux 单端）
#
# 用途说明：
#   voice_bridge 的 LLM 入站代理需要被火山 RTC 云端访问；本机 voice_bridge
#   默认 bind 127.0.0.1:18900，必须通过公网穿透暴露才能让火山回调到。
#
#   本脚本是一个最小样例，假设你已经装好 cloudflared 并执行过登录。它会启
#   一个临时隧道并把 trycloudflare.com 的 URL 打到终端；用户复制该 URL 配进
#   VOICE_BRIDGE_PUBLIC_URL 后重启 voice_bridge 即可。
#
#   产品化部署不要用此脚本——cloudflared trycloudflare URL 不固定、不限流、
#   不做鉴权，仅供本地 smoke 用。
#
# 单端事实：
#   本脚本仅 mac/linux 提供。windows 上请自行参考下面命令调用 cloudflared
#   或换 ngrok / 其他穿透工具，把生成的公网 URL 配进 VOICE_BRIDGE_PUBLIC_URL。
#   遵守 .cursor/rules/cross-platform-dev.mdc §"双端覆盖的例外"。
#
# 用法：
#   ./scripts/voice/tunnel.sh           # 用默认端口 18900
#   VOICE_BRIDGE_PORT=18901 ./scripts/voice/tunnel.sh  # 自定义端口
set -euo pipefail
cd "$(dirname "$0")/../.."

PORT="${VOICE_BRIDGE_PORT:-18900}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "[tunnel] 未检测到 cloudflared；请先安装：" >&2
  echo "  macOS:  brew install cloudflared" >&2
  echo "  Linux:  https://developers.cloudflare.com/cloudflared/install" >&2
  exit 1
fi

echo "[tunnel] 启动 cloudflared 隧道指向 http://127.0.0.1:${PORT}"
echo "[tunnel] 把输出里的 https://*.trycloudflare.com 配到 VOICE_BRIDGE_PUBLIC_URL 后重启 voice_bridge"
echo

exec cloudflared tunnel --url "http://127.0.0.1:${PORT}"
