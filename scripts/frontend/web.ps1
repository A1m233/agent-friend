# scripts/frontend/web.ps1 — 仅启动前端 web dev server（浏览器调试，不编译 Rust）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
pnpm dev @args
