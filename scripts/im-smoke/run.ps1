# scripts/im-smoke/run.ps1 — IM 通道 smoke 测试(windows)
#
# 022 · 非破坏性本机 e2e:启动 BridgeRuntime → 替 im_runtime 为 FakeIMProvider →
# 灌一条假 InboundEvent → 断言 outbound 文本非空 + session 落盘。
#
# 不接真 QQ gateway,不发真消息,不动用户数据(走 tmp AGENT_FRIEND_DATA_DIR)。
# 详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §5.2。
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

& uv run python scripts/im-smoke/smoke.py @args
