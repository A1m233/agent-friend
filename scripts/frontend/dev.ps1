# scripts/frontend/dev.ps1 — 启动桌面端开发（Tauri dev，含双窗口）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
pnpm tauri dev @args
