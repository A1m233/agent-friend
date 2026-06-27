# scripts/dev/run.ps1 — 一键起 bridge + 前端开发（windows）
#
# 默认：Tauri 桌面（含桌宠 / 对话双窗口，首次编译 Rust 较慢），启动本地 voice_bridge（不启动 cloudflared），用于 Chat Composer 语音输入 ASR。
# --web：改为浏览器 web 模式（仅 vite，不编译 Rust，热更快、但跑不出桌宠透明窗 / 多窗口）。
# --voice：启动 voice_bridge，并自动拉起 cloudflared tunnel（需安装 cloudflared 或设置 VOICE_BRIDGE_PUBLIC_URL），用于完整语音通话链路。
# --no-voice：不启动 voice_bridge（保留给显式声明 / 兼容旧命令）。
# Windows Tauri 默认暴露 WebView2 CDP 到 127.0.0.1:9222，供 desktop-visual DOM / 截图验证；可用 --no-cdp 关闭，或 --cdp-port <port> 改端口。
# 其余参数透传给前端命令（pnpm tauri dev / pnpm dev）。
# bridge 默认听 127.0.0.1:18800；voice_bridge 默认听 127.0.0.1:18900；前端经 vite proxy 连它们。
# bridge / voice_bridge 与前端共用同一控制台输出（不加前缀，与 sh 端的小差异）；Ctrl+C / 退出时 taskkill /T 清理子进程树。
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$previousLocation = (Get-Location).Path

try {
Set-Location $root

$mode = "tauri"
$voiceMode = "local"
$cdpEnabled = $true
$cdpPort = 9222
$rest = @()
for ($i = 0; $i -lt $args.Count; $i++) {
    $a = $args[$i]
    switch ($a) {
        "--web" { $mode = "web" }
        "--tauri" { $mode = "tauri" }
        "--voice" { $voiceMode = "tunnel" }
        "--no-voice" { $voiceMode = "off" }
        "--no-cdp" { $cdpEnabled = $false }
        "--cdp-port" {
            $i += 1
            if ($i -ge $args.Count) {
                Write-Error "--cdp-port 需要一个端口号，例如：--cdp-port 9222"
            }
            try {
                $cdpPort = [int]$args[$i]
            }
            catch {
                Write-Error "--cdp-port 只接受数字端口，收到：$($args[$i])"
            }
            $cdpEnabled = $true
        }
        default { $rest += $a }
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 uv。先跑 .\scripts\setup\run.ps1 初始化环境。"
}
. (Join-Path $PSScriptRoot "..\frontend\_ensure-node.ps1")
Ensure-NodePnpm

# 028 · dev 脚本启动前端前，若 1420 被上次残留进程占用，尝试自动清理。
# 不自动换端口：1420/1421 与 tauri.conf.json / vite.config.ts 多处约定耦合，换端口更易出错。
$DevPort = 1420

function Get-DevPortListeners($port) {
    try {
        return @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop)
    }
    catch {
        return @()
    }
}

function Test-PortInUse($port) {
    return (Get-DevPortListeners $port).Count -gt 0
}

function Test-HttpOk([string]$url) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return $resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

function Wait-HttpOk([string]$url, [int]$timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk $url) {
            return $true
        }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Wait-CloudflaredUrl([string[]]$logPaths, [int]$timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $pattern = "https://[a-z0-9-]+\.trycloudflare\.com"
    while ((Get-Date) -lt $deadline) {
        foreach ($path in $logPaths) {
            if (-not (Test-Path $path)) { continue }
            $content = Get-Content -Raw -ErrorAction SilentlyContinue $path
            if ($content -match $pattern) {
                return $Matches[0]
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return ""
}

function Start-VoiceTunnel {
    if (-not [string]::IsNullOrWhiteSpace($env:VOICE_BRIDGE_PUBLIC_URL)) {
        Write-Host "===> 使用当前进程 VOICE_BRIDGE_PUBLIC_URL（不读取 .env）"
        return @{ Process = $null; PublicUrl = $env:VOICE_BRIDGE_PUBLIC_URL }
    }

    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        Write-Error "未检测到 cloudflared。语音通话需要公网回调 URL；请安装 cloudflared，或在当前 shell 显式设置 VOICE_BRIDGE_PUBLIC_URL 后重试。"
    }

    $outLog = Join-Path $env:TEMP "agent-friend-cloudflared-dev.log"
    $errLog = Join-Path $env:TEMP "agent-friend-cloudflared-dev.err.log"
    Remove-Item -LiteralPath $outLog, $errLog -Force -ErrorAction SilentlyContinue

    Write-Host "===> 启动 cloudflared tunnel（127.0.0.1:18900）…"
    $proc = Start-Process `
        -FilePath "cloudflared" `
        -ArgumentList @("tunnel", "--url", "http://127.0.0.1:18900") `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru `
        -WindowStyle Hidden

    $publicUrl = Wait-CloudflaredUrl -logPaths @($outLog, $errLog) -timeoutSeconds 60
    if ([string]::IsNullOrWhiteSpace($publicUrl)) {
        if ($proc -and -not $proc.HasExited) {
            taskkill /PID $proc.Id /T /F 2>$null | Out-Null
        }
        Write-Error "cloudflared 60 秒内没有输出 trycloudflare URL。日志：$outLog / $errLog"
    }

    $env:VOICE_BRIDGE_PUBLIC_URL = $publicUrl
    Write-Host "  [OK] VOICE_BRIDGE_PUBLIC_URL=$publicUrl"
    return @{ Process = $proc; PublicUrl = $publicUrl }
}

function Test-DevPortProcess($proc, $commandLine) {
    $name = $proc.ProcessName.ToLowerInvariant()
    if ($name -in @("node", "pnpm", "npm", "tauri", "cargo", "app")) {
        return $true
    }
    if ($commandLine -match "(^|\s)(pnpm|vite|tauri|cargo)(\s|$)" -or
        $commandLine -match "frontend") {
        return $true
    }
    return $false
}

function Stop-DevPortProcess($port) {
    $conns = Get-DevPortListeners $port
    $owners = @($conns | ForEach-Object { $_.OwningProcess } | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
    foreach ($ownerPid in $owners) {
        $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $commandLine = ""
        try {
            $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction Stop).CommandLine
        }
        catch {
            # ignore
        }
        if (Test-DevPortProcess $proc $commandLine) {
            try {
                # Windows 上 pnpm/tauri/vite 常有父子进程树；只 Stop-Process
                # OwningProcess 容易留下子进程继续监听 1420。
                taskkill /PID $proc.Id /T /F 2>$null | Out-Null
            }
            catch {
                # ignore
            }
        }
    }
    # 等最多 2 秒让进程退出
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Test-PortInUse $port)) {
            return $true
        }
        Start-Sleep -Milliseconds 100
    }
    return -not (Test-PortInUse $port)
}

function Get-PortListeners($port) {
    try {
        return @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop)
    }
    catch {
        return @()
    }
}

function Get-ProcessCommandLine($processId) {
    try {
        return (Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction Stop).CommandLine
    }
    catch {
        return ""
    }
}

function Test-VoiceBridgeProcess($processId) {
    $commandLine = Get-ProcessCommandLine $processId
    return $commandLine -match "(^|\s)-m\s+voice_bridge(\s|$)" -or
        $commandLine -match "voice_bridge"
}

function Stop-VoiceBridgePortProcess($port, $reason) {
    $owners = @(Get-PortListeners $port | ForEach-Object { $_.OwningProcess } | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
    if ($owners.Count -eq 0) {
        return $true
    }

    foreach ($ownerPid in $owners) {
        if (-not (Test-VoiceBridgeProcess $ownerPid)) {
            $commandLine = Get-ProcessCommandLine $ownerPid
            Write-Error "端口 $port 已被占用，但占用进程无法确认为 voice_bridge，脚本不会自动结束它。PID=$ownerPid CommandLine=$commandLine"
        }
    }

    Write-Host "  [WARN] 端口 $port 上已有 voice_bridge，$reason；自动停止旧进程后重启…"
    foreach ($ownerPid in $owners) {
        taskkill /PID $ownerPid /T /F 2>$null | Out-Null
    }
    for ($i = 0; $i -lt 30; $i++) {
        if (-not (Test-PortInUse $port)) {
            Write-Host "  [OK] 端口 $port 已释放"
            return $true
        }
        Start-Sleep -Milliseconds 100
    }
    Write-Error "端口 $port 上的旧 voice_bridge 未能停止，请手动检查后重试。"
    return $false
}

if (Test-PortInUse $DevPort) {
    Write-Host "  [WARN] 端口 $DevPort 被占用，尝试清理上次残留的 vite/tauri 进程…"
    if (Stop-DevPortProcess $DevPort) {
        Write-Host "  [OK] 端口 $DevPort 已释放"
    }
    else {
        Write-Error "端口 $DevPort 仍被占用，无法启动前端 dev server。请检查是否有其他程序占用该端口，或手动终止相关进程后再试。"
    }
}

if ($mode -eq "tauri" -and $cdpEnabled) {
    if (Test-PortInUse $cdpPort) {
        Write-Error "WebView2 CDP 端口 $cdpPort 已被占用。请关闭占用进程，或改用 --cdp-port <port>，或传 --no-cdp。"
    }
    $flag = "--remote-debugging-port=$cdpPort"
    $currentWebViewArgs = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
    if ($null -eq $currentWebViewArgs) {
        $currentWebViewArgs = ""
    }
    $cleanWebViewArgs = ($currentWebViewArgs -replace "(^|\s)--remote-debugging-port=\S+", "").Trim()
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "$cleanWebViewArgs $flag".Trim()
    Write-Host "===> WebView2 CDP: http://127.0.0.1:$cdpPort（desktop-visual 可直接抓真实 WebView DOM / 截图）"
}
elseif ($mode -eq "tauri") {
    Write-Host "===> WebView2 CDP 已关闭（--no-cdp）"
}

$bridge = $null
$voice = $null
$cloudflared = $null

try {
Write-Host "===> 启动 bridge（127.0.0.1:18800）…"
# AGENT_BRIDGE_DEV_MODE=true 挂 /dev/* 端点（如 /dev/fire-source 立即触发主动轮），
# 让 dev-fire-source / 状态机调试 / 018 push 通道真跑可用。生产环境永不开。
$env:AGENT_BRIDGE_DEV_MODE = "true"
$bridge = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "agent_bridge") -PassThru -NoNewWindow

if ($voiceMode -ne "off") {
    $needsVoiceTunnel = $voiceMode -eq "tunnel"
    $reuseExistingVoiceBridge = $false
    $restartVoiceBridgeReason = $null
    if (Test-HttpOk "http://127.0.0.1:18900/healthz") {
        if ($needsVoiceTunnel) {
            $restartVoiceBridgeReason = "完整语音通话需要注入本次 tunnel URL"
        }
        elseif (-not (Test-HttpOk "http://127.0.0.1:18900/voice/transcriptions/healthz")) {
            $restartVoiceBridgeReason = "现有 voice_bridge 缺少语音输入转写健康端点，可能是旧进程"
        }
        else {
            Write-Host "===> voice_bridge 已在 127.0.0.1:18900 运行，复用现有进程（本地 ASR 不需要 cloudflared）"
            $reuseExistingVoiceBridge = $true
        }
    }
    elseif (Test-PortInUse 18900) {
        $restartVoiceBridgeReason = "健康检查不可用"
    }

    if (-not $reuseExistingVoiceBridge) {
        if ($needsVoiceTunnel) {
            $tunnel = Start-VoiceTunnel
            $cloudflared = $tunnel.Process
        }
        if ($restartVoiceBridgeReason) {
            Stop-VoiceBridgePortProcess 18900 $restartVoiceBridgeReason
        }
        elseif (Test-PortInUse 18900) {
            Stop-VoiceBridgePortProcess 18900 "准备启动新的 voice_bridge"
        }
        if ($needsVoiceTunnel) {
            Write-Host "===> 启动 voice_bridge（127.0.0.1:18900，完整语音通话 tunnel 模式）…"
        }
        else {
            Write-Host "===> 启动 voice_bridge（127.0.0.1:18900，本地 ASR 模式，不启动 cloudflared）…"
        }
        $voice = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "voice_bridge") -PassThru -NoNewWindow
        if (-not (Wait-HttpOk "http://127.0.0.1:18900/healthz" 10)) {
            Write-Error "voice_bridge 启动后 10 秒内未通过健康检查。请查看上方 voice_bridge 日志。"
        }
    }
}
else {
    Write-Host "===> 跳过 voice_bridge（传 --no-voice 后显式关闭语音链路）"
}

    Set-Location (Join-Path $root "frontend")
    if ($mode -eq "web") {
        Write-Host "===> 前端模式：web（浏览器调试，不编译 Rust）"
        pnpm dev @rest
    }
    else {
        Write-Host "===> 前端模式：tauri（桌面，含双窗口；首次编译 Rust 较慢）"
        pnpm tauri dev @rest
    }
}
finally {
    Write-Host "`n===> 收尾：停止 bridge / voice_bridge / cloudflared…"
    if ($bridge -and -not $bridge.HasExited) {
        # /T 连同 uv 派生的 python 进程树一起结束
        taskkill /PID $bridge.Id /T /F 2>$null | Out-Null
    }
    if ($voice -and -not $voice.HasExited) {
        taskkill /PID $voice.Id /T /F 2>$null | Out-Null
    }
    if ($cloudflared -and -not $cloudflared.HasExited) {
        taskkill /PID $cloudflared.Id /T /F 2>$null | Out-Null
    }
}
}
finally {
    Set-Location $previousLocation
}
