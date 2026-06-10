# scripts/frontend/test.ps1 — 前端单测（vitest）（windows）
# 详见 scripts/README.md。参数透传给 vitest：.\scripts\frontend\test.ps1 -t foo
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
. (Join-Path $here "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $here "..\..\frontend")
Write-Host "===> vitest"
pnpm run test @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
