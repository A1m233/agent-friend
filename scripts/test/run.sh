#!/usr/bin/env bash
# scripts/test/run.sh — 跑 pytest（mac/linux）
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run pytest "$@"
