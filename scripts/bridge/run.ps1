# scripts/bridge/run.ps1 — 启动 agent-bridge HTTP SSE 服务（windows）
#
# 参数透传给 python -m agent_bridge（本期 0 参数；端口 / host 通过 .env 配置）。
# 默认监听 127.0.0.1:18800。详见 docs/requirements/006-agent-bridge/。
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m agent_bridge @args
