#!/usr/bin/env bash
# scripts/fix/run.sh — 自动修复 lint 问题（mac/linux）
# 包含：ruff check --fix + ruff format
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "==> ruff check --fix"
uv run ruff check --fix
echo
echo "==> ruff format"
uv run ruff format
echo
echo "==> 自动修复完成"
