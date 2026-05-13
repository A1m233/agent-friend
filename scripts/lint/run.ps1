# scripts/lint/run.ps1 — lint 检查（windows）
# 包含：ruff check + ruff format --check
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

Write-Host "==> ruff check"
uv run ruff check
Write-Host ""
Write-Host "==> ruff format --check"
uv run ruff format --check
Write-Host ""
Write-Host "==> lint 全部通过"
