# scripts/check/run.ps1 — 合入 main 前的一键全绿检查（windows）
# 串联：lint（ruff check + format --check）→ typecheck（mypy strict）→ test（pytest）
# 任意一步失败立即退出。pytest 参数可透传：.\scripts\check\run.ps1 -k foo
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$here = $PSScriptRoot

Write-Host "===> [1/4] lint"
& (Join-Path $here "..\lint\run.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "===> [2/4] typecheck"
& (Join-Path $here "..\typecheck\run.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "===> [3/4] test"
& (Join-Path $here "..\test\run.ps1") @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ""
Write-Host "===> [4/4] frontend lint + test"
if ((Get-Command node -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $here "..\..\frontend\node_modules"))) {
    & (Join-Path $here "..\frontend\lint.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & (Join-Path $here "..\frontend\test.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "  [skip] 前端工具链/依赖未就绪（无 node 或未跑 frontend/install），跳过前端 lint + test"
}
Write-Host ""
Write-Host "===> check 全部通过"
