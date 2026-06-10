# scripts/lint/run.ps1 — lint 检查（windows）
# 包含：ruff check + ruff format --check
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

Write-Host "==> ruff check"
uv run ruff check
Write-Host ""
Write-Host "==> ruff format --check"
uv run ruff format --check
Write-Host ""
Write-Host "==> lint 全部通过"
