# scripts/dev-fire-source/run.ps1 — 立即触发 bridge 上指定 EventSource 的 fire_now（windows）
#
# 用法：.\scripts\dev-fire-source\run.ps1 <source_name> [bridge_url]
# 例：  .\scripts\dev-fire-source\run.ps1 cron:bedtime
#       .\scripts\dev-fire-source\run.ps1 idle_reflection http://127.0.0.1:18800
#
# 注意：仅在 bridge 配置 AGENT_BRIDGE_DEV_MODE=true 时挂载该端点；生产环境永不开。
# 详见 docs/requirements/014-engine-main-loop-and-bridge-push/。
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

if ($args.Count -lt 1) {
    Write-Error "用法：.\scripts\dev-fire-source\run.ps1 <source_name> [bridge_url]"
    exit 2
}

$sourceName = $args[0]
$url = if ($args.Count -ge 2) { $args[1] } else { "http://127.0.0.1:18800" }

$encoded = [System.Web.HttpUtility]::UrlEncode($sourceName)
$endpoint = "$url/dev/fire-source?source_name=$encoded"

Invoke-RestMethod -Method Post -Uri $endpoint
