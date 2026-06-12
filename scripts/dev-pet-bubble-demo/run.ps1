# scripts/dev-pet-bubble-demo/run.ps1 — 015 端到端 demo orchestrator（windows）
#
# 串起 014 dev-fire-source 触发链，验证 015 桌面端消费侧：
#   ✓ bedtime 主动轮在 pet 气泡冒出来；chat 窗 MessageList 没有该消息（AC-4）
#   ✓ idle_reflection silent turn 桌宠无任何反应；bridge log 有 memory.observe（AC-5）
#   ✓ 同时 user 在 chat 窗发对话不受影响（AC-1 / AC-8）
#
# 前置：
#   1. bridge 已起且 AGENT_BRIDGE_DEV_MODE=true（参 scripts/bridge）
#   2. frontend 桌面端已起（.\scripts\frontend\dev.ps1 —— pet 窗 + chat 窗都开）
#   3. 已获用户授权进行真 LLM 调用（参 .cursor/rules/llm-api-confirm；
#      触发会导致 bedtime 走一次完整 inner loop + DeepSeek 调用）
#
# 用法：.\scripts\dev-pet-bubble-demo\run.ps1 [bridge_url]
# 详见 docs/requirements/015-desktop-pet-bubble-and-conversation-owner/。
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

$url = if ($args.Count -ge 1) { $args[0] } else { "http://127.0.0.1:18800" }

Write-Host "===> [1/2] 触发 BedtimeSource（cron:bedtime）"
Write-Host "    期望（AC-4）：桌宠头上冒出气泡；chat 窗 MessageList 没有这条消息"
& ".\scripts\dev-fire-source\run.ps1" "cron:bedtime" $url
Write-Host ""
Write-Host "(等待 6 秒让主动轮跑完 inner loop + 气泡渲染)"
Start-Sleep -Seconds 6

Write-Host "===> [2/2] 触发 IdleReflectionSource"
Write-Host "    期望（AC-5）：桌宠无任何反应（silent turn 在 owner 层被丢弃）"
Write-Host "    bridge 日志应有 memory.observe 被调用"
& ".\scripts\dev-fire-source\run.ps1" "idle_reflection" $url
Write-Host ""
Start-Sleep -Seconds 3

Write-Host "===> demo orchestrator 完成"
Write-Host "请人工核对以下端到端现象："
Write-Host "  [AC-4]  桌宠头上冒出 bedtime 气泡"
Write-Host "  [AC-4]  chat 窗 MessageList 没有 bedtime 消息"
Write-Host "  [AC-5]  idle_reflection 没冒气泡 / 桌宠表情不变"
Write-Host "  [AC-5]  bridge log 有 memory.observe 调用"
Write-Host "  [AC-1]  在 chat 窗发条 user 消息：MessageList 正常流式渲染、pet 气泡不跟随"
Write-Host "  [AC-7]  切到另一个 Space / Virtual Desktop 桌宠仍可见（macOS spike 优先）"
Write-Host "  [AC-7]  启动全屏视频播放器桌宠仍浮在视频之上（macOS spike 优先）"
Write-Host "  [AC-6]  切走历史会话再切回：chat 窗 MessageList 不含 bedtime 主动轮消息"
