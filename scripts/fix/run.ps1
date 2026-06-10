# scripts/fix/run.ps1 — 自动修复 lint 问题（windows）
# 包含：ruff check --fix + ruff format
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

Write-Host "==> ruff check --fix"
uv run ruff check --fix
Write-Host ""
Write-Host "==> ruff format"
uv run ruff format
Write-Host ""
Write-Host "==> 自动修复完成"
