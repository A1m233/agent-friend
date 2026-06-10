# scripts/frontend/lint.ps1 — 前端 lint（ESLint + 颜色门禁）+ 类型检查（tsc）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$here = $PSScriptRoot
. (Join-Path $here "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $here "..\..\frontend")
Write-Host "===> eslint"
pnpm run lint
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "===> color-guard"
pnpm run lint:colors
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "===> typecheck"
pnpm run typecheck
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
