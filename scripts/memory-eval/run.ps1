# scripts/memory-eval/run.ps1 — 跑 memory 召回评测 PoC（默认 PerLTQA 中文；windows）
# ⚠️ 会触发真实 LLM 抽取调用；运行前确保已授权 + 项目根 .env 配好 DEEPSEEK_API_KEY
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m memory_eval @args
