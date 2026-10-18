# scripts/im-smoke/run.ps1 — IM 通道 smoke 测试(windows)
#
# 022/067 · 非破坏性本机 e2e:启动 BridgeRuntime → fake Agent + FakeIMProvider →
# 灌一条假 InboundEvent → 断言流式 update/DONE + 独立菜单且正文不重复。
#
# 默认不接真 QQ gateway、不发真消息，使用 tmp AGENT_FRIEND_DATA_DIR。
# --live-qq：155 固定合成回合验收；先 --preflight，实际发送须 --allow-send。
# 需停止已有 QQ owner；手机发送 AF155-A/B/C，报告写 --output，不调用 LLM。
# --cases B 可只复验 B；最多等待 900 秒。
# --live-qq-approvals --case modes|files|commands|pending|preview: no LLM.
# 详见 docs/requirements/022-im-channel-and-qq-adapter/design.md §5.2。
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

if ($args.Count -gt 0 -and $args[0] -eq "--live-qq-approvals") {
    $liveArgs = @($args | Select-Object -Skip 1)
    & uv run python scripts/im-smoke/qq_approvals.py @liveArgs
} elseif ($args.Count -gt 0 -and $args[0] -eq "--live-qq") {
    $liveArgs = @($args | Select-Object -Skip 1)
    & uv run python scripts/im-smoke/qq_delivery.py @liveArgs
} else {
    & uv run python scripts/im-smoke/smoke.py @args
}
exit $LASTEXITCODE
