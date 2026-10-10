# scripts/frontend/dev.ps1 — 启动桌面端开发（Tauri dev，含双窗口）（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
Set-Location $repoRoot
. (Join-Path $PSScriptRoot "..\_lib\python312.ps1")
$python = Get-BuildPython
& $python -B (Join-Path $repoRoot "scripts\build-storage\execute.py") --cwd (Join-Path $repoRoot "frontend") dev -- node (Join-Path $repoRoot "frontend\node_modules\@tauri-apps\cli\tauri.js") dev @args
exit $LASTEXITCODE
