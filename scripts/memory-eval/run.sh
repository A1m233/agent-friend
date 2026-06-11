#!/usr/bin/env bash
# scripts/memory-eval/run.sh — 跑 memory 召回评测 PoC（默认 PerLTQA 中文；mac/linux）
# ⚠️ 会触发真实 LLM 抽取调用；运行前确保已授权 + 项目根 .env 配好 DEEPSEEK_API_KEY
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m memory_eval "$@"
