# scripts/fix/run.ps1 — 自动修复 lint 问题（windows）
# 包含：ruff check --fix + ruff format
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

Write-Host "==> ruff check --fix"
uv run ruff check --fix
Write-Host ""
Write-Host "==> ruff format"
uv run ruff format
Write-Host ""
Write-Host "==> 自动修复完成"
