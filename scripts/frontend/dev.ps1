# scripts/frontend/dev.ps1 — 启动桌面端开发（Tauri dev，含双窗口）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
pnpm tauri dev @args
