# scripts/frontend/lint.ps1 — 前端 lint（ESLint）+ 类型检查（tsc）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
. (Join-Path $here "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $here "..\..\frontend")
Write-Host "===> eslint"
pnpm run lint
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "===> typecheck"
pnpm run typecheck
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
