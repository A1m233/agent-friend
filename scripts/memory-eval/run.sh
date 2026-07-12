#!/usr/bin/env bash
# scripts/memory-eval/run.sh — 跑 memory 召回评测 PoC（默认 PerLTQA 中文；mac/linux）
# ⚠️ 会触发真实 LLM 抽取调用；运行前确保已授权，默认使用 runtime_config 凭证中心
# 读取存量 .env 时需显式设置 AGENT_FRIEND_CONFIG_MODE=legacy-dotenv
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m memory_eval "$@"
