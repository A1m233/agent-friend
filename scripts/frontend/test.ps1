# scripts/frontend/test.ps1 — 前端单测（vitest）（windows）
# 详见 scripts/README.md。参数透传给 vitest：.\scripts\frontend\test.ps1 -t foo
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$here = $PSScriptRoot
. (Join-Path $here "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $here "..\..\frontend")
Write-Host "===> vitest"
$oldEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
pnpm run test @args 2>&1 | ForEach-Object { Write-Host $_ }
$ErrorActionPreference = $oldEAP
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
