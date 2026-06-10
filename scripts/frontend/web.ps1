# scripts/frontend/web.ps1 — 仅启动前端 web dev server（浏览器调试，不编译 Rust）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
pnpm dev @args
