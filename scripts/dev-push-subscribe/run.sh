#!/usr/bin/env bash
# scripts/dev-push-subscribe/run.sh — 订阅 bridge /push/subscribe（mac/linux）
#
# 用法：./scripts/dev-push-subscribe/run.sh [--url URL] [--kinds KINDS] [--verbose]
# 默认连本机 bridge（http://127.0.0.1:18800）；详见 docs/requirements/014-engine-main-loop-and-bridge-push/。
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m agent_bridge.dev.push_subscribe "$@"
