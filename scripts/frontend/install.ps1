# scripts/frontend/install.ps1 — 安装前端依赖（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_ensure-node.ps1")

Write-Host "==> 检查 node / pnpm"
Ensure-NodePnpm
Write-Host "  node $(node -v) / pnpm $(pnpm -v)"

Write-Host ""
Write-Host "==> 安装前端依赖（pnpm install @ frontend/）"
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
pnpm install @args
