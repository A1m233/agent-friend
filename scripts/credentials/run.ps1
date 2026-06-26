# scripts/credentials/run.ps1 — 管理 agent-friend 本机系统凭据（windows）
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m runtime_config @args
