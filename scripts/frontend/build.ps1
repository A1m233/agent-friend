# scripts/frontend/build.ps1 — 构建桌面端安装包（Tauri build）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
pnpm tauri build @args
