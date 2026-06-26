# voice_bridge/smoke/run.ps1 — Windows 端到端语音 smoke 一键启动
#
# 本脚本会按顺序拉起：
#   1. agent_bridge
#   2. cloudflared quick tunnel（自动生成公网 URL）
#   3. voice_bridge（使用 cloudflared URL）
#   4. 本地 HTTP server（在 127.0.0.1:8765  serve index.html）
#
# 使用步骤：
#   1. 运行 .\scripts\credentials\run.ps1 set-bundle deepseek
#   2. 运行 .\scripts\credentials\run.ps1 set-bundle volc-voice
#   3. 在 PowerShell 里跑：.\scripts\voice-smoke\run.ps1
#   4. 等待日志出现 "打开 http://127.0.0.1:8765/voice_bridge/smoke/"
#   5. 在浏览器里点 "拨打通话"
#   6. 挂断后回到本窗口按 Enter，脚本会清理所有进程
#
# ⚠️ 会触发真实火山 RTC + DeepSeek LLM 调用，产生 token 消耗。

$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$here = $PSScriptRoot
$repo = Resolve-Path (Join-Path $here "..\..")
Set-Location $repo

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

function Wait-HttpOk([string]$url, [int]$timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "等待服务健康检查超时：$url"
}

function Assert-PortFree([int]$port) {
    $lines = netstat -ano | Select-String ":$port"
    $listening = @($lines | Where-Object { $_.Line -match "\sLISTENING\s+(\d+)$" })
    if ($listening.Count -gt 0) {
        $pids = $listening | ForEach-Object {
            if ($_.Line -match "\sLISTENING\s+(\d+)$") { $Matches[1] }
        } | Sort-Object -Unique
        throw "端口 $port 已被占用，PID: $($pids -join ', ')。请先停止旧的 smoke/bridge/voice/http server 后重试。"
    }
}

# ----- 前置检查 -----

$required = @("uv", "cloudflared")
foreach ($cmd in $required) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "错误：缺少命令 $cmd，请先安装" -ForegroundColor Red
        exit 1
    }
}

foreach ($port in @(18800, 18900, 8765)) {
    Assert-PortFree $port
}

Write-Step "当前凭据状态（不显示明文）"
uv run python -m runtime_config status

# 提醒用户这是真机调用
Write-Host "本脚本会启动 agent_bridge + voice_bridge + cloudflared，并触发真实火山 RTC 与 DeepSeek LLM 调用。" -ForegroundColor Yellow
Confirm-Continue "确认继续"

$processes = @()
$cloudflaredLog = Join-Path $env:TEMP "agent-friend-cloudflared-smoke.log"
$cloudflaredErrLog = Join-Path $env:TEMP "agent-friend-cloudflared-smoke.err.log"

try {
    # ----- 1. agent_bridge -----
    Write-Step "启动 agent_bridge（新窗口）"
    $agentProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$repo`"; .\scripts\bridge\run.ps1" -PassThru
    $processes += $agentProc
    Write-Host "agent_bridge PID: $($agentProc.Id)"
    Write-Host "等待 agent_bridge /healthz ..."
    Wait-HttpOk "http://127.0.0.1:18800/healthz" 30

    # ----- 2. cloudflared -----
    Write-Step "启动 cloudflared quick tunnel（等待公网 URL）"
    if (Test-Path $cloudflaredLog) { Remove-Item $cloudflaredLog -Force }
    if (Test-Path $cloudflaredErrLog) { Remove-Item $cloudflaredErrLog -Force }
    $cloudflaredProc = Start-Process cloudflared -ArgumentList "tunnel", "--url", "http://127.0.0.1:18900" -RedirectStandardOutput $cloudflaredLog -RedirectStandardError $cloudflaredErrLog -PassThru
    $processes += $cloudflaredProc

    $publicUrl = $null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        $logPaths = @($cloudflaredLog, $cloudflaredErrLog)
        foreach ($logPath in $logPaths) {
            if (Test-Path $logPath) {
                $match = Select-String -Path $logPath -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
                if ($match) {
                    $publicUrl = $match.Matches[0].Value
                    break
                }
            }
        }
        if ($publicUrl) { break }
    }

    if (-not $publicUrl) {
        throw "cloudflared 公网 URL 获取失败，请检查 $cloudflaredLog 或 $cloudflaredErrLog"
    }
    Write-Host "cloudflared URL: $publicUrl" -ForegroundColor Green

    # ----- 给 voice_bridge 子进程注入公网 URL -----
    Write-Step "设置 VOICE_BRIDGE_PUBLIC_URL（仅当前脚本进程，不写 .env）"
    $env:VOICE_BRIDGE_PUBLIC_URL = $publicUrl
    Write-Host "VOICE_BRIDGE_PUBLIC_URL=$publicUrl"

    # ----- 3. voice_bridge -----
    Write-Step "启动 voice_bridge（新窗口）"
    $voiceProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$repo`"; .\scripts\voice\run.ps1" -PassThru
    $processes += $voiceProc
    Write-Host "voice_bridge PID: $($voiceProc.Id)"
    Write-Host "等待 voice_bridge /healthz ..."
    Wait-HttpOk "http://127.0.0.1:18900/healthz" 30

    # ----- 4. HTTP server -----
    Write-Step "启动本地 HTTP server（127.0.0.1:8765）"
    $httpProc = Start-Process uv -ArgumentList "run", "python", "-m", "http.server", "8765", "--bind", "127.0.0.1" -WorkingDirectory $repo -PassThru -WindowStyle Hidden
    $processes += $httpProc
    Write-Host "HTTP server PID: $($httpProc.Id)"
    Write-Host "等待 smoke 页面 HTTP server ..."
    Wait-HttpOk "http://127.0.0.1:8765/voice_bridge/smoke/" 30

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
    if (Test-Path $cloudflaredErrLog) {
        Remove-Item $cloudflaredErrLog -Force -ErrorAction SilentlyContinue
    }
    Write-Host "清理完成"
}
