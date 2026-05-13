# scripts/typecheck/run.ps1 — mypy 类型检查（windows）
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run mypy llm_providers/ tools/ agent/ memory/
