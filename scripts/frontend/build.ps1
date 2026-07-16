# scripts/frontend/build.ps1 — 构建生产前端静态资源（windows）
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
. (Join-Path $PSScriptRoot "_ensure-node.ps1")
Ensure-NodePnpm
Set-Location (Join-Path $PSScriptRoot "..\..\frontend")
pnpm run build @args
