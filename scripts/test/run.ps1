# scripts/test/run.ps1 — 跑 pytest（windows）
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run pytest @args
