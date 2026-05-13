# scripts/cli/run.ps1 — 启动 agent-friend CLI 调试 UI（windows）
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m tools.cli @args
