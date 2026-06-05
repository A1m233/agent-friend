# scripts/voice/run.ps1 — 启动 voice-bridge 控制平面 + LLM 入站代理（windows）
#
# 参数透传给 python -m voice_bridge（本期 0 参数；端口 / host / 火山凭证通过 .env 配置）。
# 默认监听 127.0.0.1:18900。详见 docs/requirements/007-voice-call/。
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
uv run python -m voice_bridge @args
