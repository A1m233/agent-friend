#!/usr/bin/env bash
# scripts/im-smoke/run.sh — IM 通道 smoke 测试(mac/linux)
#
# 022/067 · 非破坏性本机 e2e:启动 BridgeRuntime → fake Agent + FakeIMProvider →
# 灌一条假 InboundEvent → 断言流式 update/DONE + 独立菜单且正文不重复。
#
# 默认不接真 QQ gateway、不发真消息，使用 tmp AGENT_FRIEND_DATA_DIR。
# --live-qq：155 固定合成回合验收；先 --preflight，实际发送须 --allow-send。
# 需停止已有 QQ owner；手机发送 AF155-A/B/C，报告写 --output，不调用 LLM。
# --cases B 可只复验 B；最多等待 900 秒。
# 详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §5.2。
set -euo pipefail
cd "$(dirname "$0")/../.."

if [[ "${1:-}" == "--live-qq" ]]; then
  shift
  exec uv run python scripts/im-smoke/qq_delivery.py "$@"
fi
exec uv run python scripts/im-smoke/smoke.py "$@"
