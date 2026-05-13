#!/usr/bin/env bash
# scripts/lint/run.sh — lint 检查（mac/linux）
# 包含：ruff check + ruff format --check
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "==> ruff check"
uv run ruff check
echo
echo "==> ruff format --check"
uv run ruff format --check
echo
echo "==> lint 全部通过"
