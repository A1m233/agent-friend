# scripts/check/run.ps1 — 合入 main 前的一键全绿检查（windows）
# 串联：lint（ruff check + format --check）→ typecheck（mypy strict）→ test（pytest）
# 任意一步失败立即退出。pytest 参数可透传：.\scripts\check\run.ps1 -k foo
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

Write-Host "===> [1/3] lint"
& (Join-Path $here "..\lint\run.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "===> [2/3] typecheck"
& (Join-Path $here "..\typecheck\run.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "===> [3/3] test"
& (Join-Path $here "..\test\run.ps1") @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "===> check 全部通过"
