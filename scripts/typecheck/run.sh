#!/usr/bin/env bash
# scripts/typecheck/run.sh — mypy 类型检查（mac/linux）
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run mypy llm_providers/ tools/ agent/ memory/ agent_bridge/ voice_bridge/
