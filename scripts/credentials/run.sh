#!/usr/bin/env bash
# scripts/credentials/run.sh — 管理 agent-friend 本机系统凭据（mac/linux）
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m runtime_config "$@"
