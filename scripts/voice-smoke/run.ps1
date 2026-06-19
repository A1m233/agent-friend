# voice_bridge/smoke/run.ps1 — Windows 端到端语音 smoke 一键启动
#
# 本脚本会按顺序拉起：
#   1. agent_bridge
#   2. cloudflared quick tunnel（自动生成公网 URL）
#   3. voice_bridge（使用 cloudflared URL）
#   4. 本地 HTTP server（在 127.0.0.1:8765  serve index.html）
#
# 使用步骤：
#   1. 确认 .env 已填好 DEEPSEEK_API_KEY、VOLC_* 等凭证
#   2. 在 PowerShell 里跑：.\scripts\voice-smoke\run.ps1
#   3. 等待日志出现 "打开 http://127.0.0.1:8765/voice_bridge/smoke/"
#   4. 在浏览器里点 "拨打通话"
#   5. 挂断后回到本窗口按 Enter，脚本会清理所有进程
#
# ⚠️ 会触发真实火山 RTC + DeepSeek LLM 调用，产生 token 消耗。

$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$here = $PSScriptRoot
$repo = Resolve-Path (Join-Path $here "..")
$envFile = Join-Path $repo ".env"

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "===> $msg" -ForegroundColor Cyan
}

function Confirm-Continue([string]$prompt) {
    Write-Host ""
    $answer = Read-Host "$prompt (y/N)"
    if ($answer -notmatch "^[Yy]") {
        Write-Host "已取消"
        exit 0
    }
}

# ----- 前置检查 -----

if (-not (Test-Path $envFile)) {
    Write-Host "错误：找不到 $envFile" -ForegroundColor Red
    Write-Host "请先运行 .\scripts\setup\run.ps1 生成 .env 并填入凭证"
    exit 1
}

$required = @("uv", "cloudflared")
foreach ($cmd in $required) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "错误：缺少命令 $cmd，请先安装" -ForegroundColor Red
        exit 1
    }
}

# 提醒用户这是真机调用
Write-Host "本脚本会启动 agent_bridge + voice_bridge + cloudflared，并触发真实火山 RTC 与 DeepSeek LLM 调用。" -ForegroundColor Yellow
Confirm-Continue "确认继续"

$processes = @()
$cloudflaredLog = Join-Path $env:TEMP "agent-friend-cloudflared-smoke.log"

try {
    # ----- 1. agent_bridge -----
    Write-Step "启动 agent_bridge（新窗口）"
    $agentProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$repo`"; .\scripts\bridge\run.ps1" -PassThru
    $processes += $agentProc
    Write-Host "agent_bridge PID: $($agentProc.Id)"
    Write-Host "等待 3 秒让 agent_bridge 初始化..."
    Start-Sleep -Seconds 3

    # ----- 2. cloudflared -----
    Write-Step "启动 cloudflared quick tunnel（等待公网 URL）"
    if (Test-Path $cloudflaredLog) { Remove-Item $cloudflaredLog -Force }
    $cloudflaredProc = Start-Process cloudflared -ArgumentList "tunnel", "--url", "http://127.0.0.1:18900" -RedirectStandardOutput $cloudflaredLog -RedirectStandardError $cloudflaredLog -PassThru
    $processes += $cloudflaredProc

    $publicUrl = $null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $cloudflaredLog) {
            $match = Select-String -Path $cloudflaredLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($match) {
                $publicUrl = $match.Matches[0].Value
                break
            }
        }
    }

    if (-not $publicUrl) {
        throw "cloudflared 公网 URL 获取失败，请检查 $cloudflaredLog"
    }
    Write-Host "cloudflared URL: $publicUrl" -ForegroundColor Green

    # ----- 更新 .env -----
    Write-Step "更新 .env 的 VOICE_BRIDGE_PUBLIC_URL"
    $raw = Get-Content $envFile -Raw
    $raw = $raw -replace "(?m)^VOICE_BRIDGE_PUBLIC_URL=.*$", "VOICE_BRIDGE_PUBLIC_URL=$publicUrl"
    if ($raw -notmatch "(?m)^VOICE_BRIDGE_PUBLIC_URL=") {
        $raw += "`nVOICE_BRIDGE_PUBLIC_URL=$publicUrl`n"
    }
    Set-Content -Path $envFile -Value $raw -NoNewline
    Write-Host "VOICE_BRIDGE_PUBLIC_URL 已设置为 $publicUrl"

    # ----- 3. voice_bridge -----
    Write-Step "启动 voice_bridge（新窗口）"
    $voiceProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$repo`"; .\scripts\voice\run.ps1" -PassThru
    $processes += $voiceProc
    Write-Host "voice_bridge PID: $($voiceProc.Id)"
    Write-Host "等待 3 秒让 voice_bridge 初始化..."
    Start-Sleep -Seconds 3

    # ----- 4. HTTP server -----
    Write-Step "启动本地 HTTP server（127.0.0.1:8765）"
    $httpProc = Start-Process python -ArgumentList "-m", "http.server", "8765", "--bind", "127.0.0.1" -WorkingDirectory $repo -PassThru -WindowStyle Hidden
    $processes += $httpProc
    Write-Host "HTTP server PID: $($httpProc.Id)"

    # ----- 用户指引 -----
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  smoke 环境已就绪" -ForegroundColor Green
    Write-Host "  请打开：http://127.0.0.1:8765/voice_bridge/smoke/" -ForegroundColor Green
    Write-Host ""
    Write-Host "  操作提示：" -ForegroundColor Green
    Write-Host "    1. 页面顶部 voice_bridge URL 保持默认 http://127.0.0.1:18900" -ForegroundColor Green
    Write-Host "    2. session_id 留空即可（会新建 session 并落盘）" -ForegroundColor Green
    Write-Host "    3. 点击'拨打通话'，授权麦克风" -ForegroundColor Green
    Write-Host "    4. 通话结束后点击'挂断'" -ForegroundColor Green
    Write-Host "    5. 回到本窗口按 Enter 停止所有服务" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""

    $null = Read-Host "按 Enter 停止所有服务"
}
finally {
    Write-Step "清理进程"
    foreach ($proc in $processes) {
        if ($proc -and -not $proc.HasExited) {
            try {
                Stop-Process -Id $proc.Id -Force
                Write-Host "已停止 PID $($proc.Id)"
            }
            catch {
                Write-Host "停止 PID $($proc.Id) 失败（可能已退出）：$($_.Exception.Message)"
            }
        }
    }
    if (Test-Path $cloudflaredLog) {
        Remove-Item $cloudflaredLog -Force -ErrorAction SilentlyContinue
    }
    Write-Host "清理完成"
}
