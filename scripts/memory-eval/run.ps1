# scripts/memory-eval/run.ps1 — 跑 memory 召回评测 PoC（默认 PerLTQA 中文；windows）
# ⚠️ 会触发真实 LLM 抽取调用；运行前确保已授权，默认使用 runtime_config 凭证中心
# 读取存量 .env 时需显式设置 AGENT_FRIEND_CONFIG_MODE=legacy-dotenv
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m memory_eval @args
