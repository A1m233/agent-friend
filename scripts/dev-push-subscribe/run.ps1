# scripts/dev-push-subscribe/run.ps1 — 订阅 bridge /push/subscribe（windows）
#
# 用法：.\scripts\dev-push-subscribe\run.ps1 [--url URL] [--kinds KINDS] [--verbose]
# 默认连本机 bridge（http://127.0.0.1:18800）；详见 docs/requirements/014-engine-main-loop-and-bridge-push/。
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m agent_bridge.dev.push_subscribe @args
