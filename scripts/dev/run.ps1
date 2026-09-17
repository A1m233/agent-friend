# scripts/dev/run.ps1 — 一键起 bridge + 前端开发（windows）
#
# 默认：Tauri 桌面（含桌宠 / 对话双窗口，首次编译 Rust 较慢），启动本地 voice_bridge（不启动 cloudflared），用于 Chat Composer 语音输入 ASR。
# --web：改为浏览器 web 模式（仅 vite，不编译 Rust，热更快、但跑不出桌宠透明窗 / 多窗口）。
# --voice：严格完整语音通话链路；先拿到公网回调 URL，再启动后续进程，cloudflared / public URL 不可用则 fail fast。
#          如需复用当前 VOICE_BRIDGE_PUBLIC_URL，显式设置 AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL=1。
# --no-voice：不启动 voice_bridge（保留给显式声明 / 兼容旧命令）。
# Windows Tauri 默认暴露 WebView2 CDP 到 127.0.0.1:9222，供 desktop-visual DOM / 截图验证；可用 --no-cdp 关闭，或 --cdp-port <port> 改端口。
# 其余参数透传给前端命令（pnpm tauri dev / pnpm dev）。
# bridge 默认听 127.0.0.1:18800；voice_bridge 默认听 127.0.0.1:18900；前端经 vite proxy 连它们。
# bridge / voice_bridge 与前端共用同一控制台输出（不加前缀，与 sh 端的小差异），并额外镜像到日志目录下的 dev-bridge.log / dev-voice.log；Ctrl+C / 退出时 taskkill /T 清理子进程树。
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
$configMode = if ([string]::IsNullOrWhiteSpace($env:AGENT_FRIEND_CONFIG_MODE)) { "desktop" } else { $env:AGENT_FRIEND_CONFIG_MODE }
$devCommand = "start"
$inputArgs = @($args)
if ($inputArgs.Count -gt 0 -and @("status", "stop", "takeover") -contains $inputArgs[0]) {
    $devCommand = $inputArgs[0]
    if ($inputArgs.Count -gt 1) {
        $inputArgs = @($inputArgs[1..($inputArgs.Count - 1)])
    }
    else {
        $inputArgs = @()
    }
}
$rest = @()
for ($i = 0; $i -lt $inputArgs.Count; $i++) {
    $a = $inputArgs[$i]
    if ($a -like "--config-mode=*") {
        $configMode = $a.Substring("--config-mode=".Length)
        continue
    }
    switch ($a) {
        "--web" { $mode = "web" }
        "--tauri" { $mode = "tauri" }
        "--voice" { $voiceMode = "tunnel" }
        "--no-voice" { $voiceMode = "off" }
        "--no-cdp" { $cdpEnabled = $false }
        "--cdp-port" {
            $i += 1
            if ($i -ge $inputArgs.Count) {
                Write-Error "--cdp-port 需要一个端口号，例如：--cdp-port 9222"
            }
            try {
                $cdpPort = [int]$inputArgs[$i]
            }
            catch {
                Write-Error "--cdp-port 只接受数字端口，收到：$($inputArgs[$i])"
            }
            $cdpEnabled = $true
        }
        "--legacy-dotenv" { $configMode = "legacy-dotenv" }
        default { $rest += $a }
    }
}
$env:AGENT_FRIEND_CONFIG_MODE = $configMode
$env:AGENT_FRIEND_REPO_ROOT = $root
if ([string]::IsNullOrWhiteSpace($env:AGENT_FRIEND_LOG_DIR)) {
    $env:AGENT_FRIEND_LOG_DIR = Join-Path $env:LOCALAPPDATA "agent-friend\Logs"
}
New-Item -ItemType Directory -Force -Path $env:AGENT_FRIEND_LOG_DIR | Out-Null
$devBridgeLog = Join-Path $env:AGENT_FRIEND_LOG_DIR "dev-bridge.log"
$devVoiceLog = Join-Path $env:AGENT_FRIEND_LOG_DIR "dev-voice.log"
$devTauriLog = Join-Path $env:AGENT_FRIEND_LOG_DIR "tauri.log.dev"
$foregroundLogPath = Join-Path $root "scripts\dev\foreground_log.py"
Write-Host "===> 日志目录：$env:AGENT_FRIEND_LOG_DIR"
$runtimeGuardPath = Join-Path $root "scripts\dev\runtime_guard.py"
$runtimeLeaseAcquired = $false
$lastRuntimeGuardExitCode = 0

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 uv。先跑 .\scripts\setup\run.ps1 初始化环境。"
}

function Get-DevRuntimePorts {
    $ports = @(1420, 1421, 18800, 18900)
    if ($mode -eq "tauri" -and $cdpEnabled) {
        $ports += $cdpPort
    }
    return @($ports | Sort-Object -Unique)
}

function Invoke-DevRuntimeGuard([string]$command) {
    $portsText = (Get-DevRuntimePorts) -join ","
    & uv run python $runtimeGuardPath $command --worktree $root --mode $mode --ports $portsText
    $script:lastRuntimeGuardExitCode = $LASTEXITCODE
}

if ($devCommand -eq "status") {
    Invoke-DevRuntimeGuard "status"
    if ($lastRuntimeGuardExitCode -ne 0) { exit $lastRuntimeGuardExitCode }
    return
}
if ($devCommand -eq "stop") {
    Invoke-DevRuntimeGuard "stop"
    if ($lastRuntimeGuardExitCode -ne 0) { exit $lastRuntimeGuardExitCode }
    return
}

Write-Host "===> 确保命令运行时"
$commandRuntimeOutput = @(& uv run python scripts/command-runtime/run.py ensure)
$commandRuntimeExitCode = $LASTEXITCODE
$codexExecutable = (($commandRuntimeOutput | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
if ($commandRuntimeExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($codexExecutable)) {
    Write-Error "命令运行时准备失败，dev 未启动：$codexExecutable"
}
$env:AGENT_FRIEND_CODEX_EXECUTABLE = $codexExecutable
Write-Host "  [OK] $codexExecutable"
$appLauncherOutput = @(& uv run python scripts/desktop-application/run.py ensure)
if ($LASTEXITCODE -ne 0) { throw "Application launcher build failed" }
$env:AGENT_FRIEND_APP_LAUNCHER = (($appLauncherOutput | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()

$guardCommand = if ($devCommand -eq "takeover") { "takeover" } else { "acquire" }
$runtimePortsText = (Get-DevRuntimePorts) -join ","
& uv run python $runtimeGuardPath $guardCommand --worktree $root --mode $mode --ports $runtimePortsText --owner-pid $PID
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
$runtimeLeaseAcquired = $true

. (Join-Path $PSScriptRoot "..\frontend\_ensure-node.ps1")
Ensure-NodePnpm

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

function Get-LogTail([string[]]$logPaths, [int]$lineCount = 50) {
    $blocks = @()
    foreach ($path in $logPaths) {
        if (-not (Test-Path $path)) {
            $blocks += "===== $path (missing) ====="
            continue
        }
        $lines = @(Get-Content -Tail $lineCount -Encoding UTF8 -ErrorAction SilentlyContinue $path)
        if ($lines.Count -eq 0) {
            $blocks += "===== $path (empty) ====="
        }
        else {
            $blocks += "===== $path (last $lineCount lines) ====="
            $blocks += $lines
        }
    }
    return ($blocks -join [Environment]::NewLine)
}

function Wait-CloudflaredUrl([string[]]$logPaths, [int]$timeoutSeconds, $process = $null) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $pattern = "https://[a-z0-9-]+\.trycloudflare\.com"
    while ((Get-Date) -lt $deadline) {
        foreach ($path in $logPaths) {
            if (-not (Test-Path $path)) { continue }
            $content = Get-Content -Raw -Encoding UTF8 -ErrorAction SilentlyContinue $path
            if ($content -match $pattern) {
                return $Matches[0]
            }
        }
        if ($process -and $process.HasExited) {
            return ""
        }
        Start-Sleep -Milliseconds 500
    }
    return ""
}

function Start-VoiceTunnel {
    if (-not [string]::IsNullOrWhiteSpace($env:VOICE_BRIDGE_PUBLIC_URL)) {
        if ($env:AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL -eq "1") {
            Write-Host "===> 复用当前进程 VOICE_BRIDGE_PUBLIC_URL（AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL=1，不读取 .env）"
            return @{ Process = $null; PublicUrl = $env:VOICE_BRIDGE_PUBLIC_URL }
        }
        Write-Host "===> 忽略当前进程已有 VOICE_BRIDGE_PUBLIC_URL；--voice 将启动新的 cloudflared tunnel"
        Write-Host "     如确需复用外部公网 URL，请设置 AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL=1"
        $env:VOICE_BRIDGE_PUBLIC_URL = ""
    }

    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        Write-Error "未检测到 cloudflared。语音通话需要公网回调 URL；请安装 cloudflared，或在当前 shell 显式设置 VOICE_BRIDGE_PUBLIC_URL 和 AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL=1 后重试。"
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

    $publicUrl = Wait-CloudflaredUrl -logPaths @($outLog, $errLog) -timeoutSeconds 60 -process $proc
    if ([string]::IsNullOrWhiteSpace($publicUrl)) {
        $reason = "cloudflared 60 秒内没有输出 trycloudflare URL。"
        if ($proc -and $proc.HasExited) {
            $reason = "cloudflared 在输出 trycloudflare URL 前退出，exit code=$($proc.ExitCode)。"
        }
        $tail = Get-LogTail -logPaths @($outLog, $errLog) -lineCount 50
        if ($proc -and -not $proc.HasExited) {
            Stop-ProcessTreeIfPresent $proc.Id | Out-Null
        }
        Write-Error "$reason`n日志：$outLog / $errLog`n最近日志：`n$tail`n提示：--voice 需要完整公网语音回调链路，因此 tunnel 失败会 fail fast。Cloudflare quick tunnel 返回 5xx/1101 时通常是临时服务端失败，可稍后重试，或设置 VOICE_BRIDGE_PUBLIC_URL + AGENT_FRIEND_DEV_REUSE_VOICE_PUBLIC_URL=1 复用稳定公网 URL。"
    }

    $env:VOICE_BRIDGE_PUBLIC_URL = $publicUrl
    Write-Host "  [OK] VOICE_BRIDGE_PUBLIC_URL=$publicUrl"
    return @{ Process = $proc; PublicUrl = $publicUrl }
}

function Stop-ProcessTreeIfPresent($processId) {
    if (-not $processId -or [int]$processId -le 0) {
        return $true
    }
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
        return $true
    }
    try {
        taskkill /PID $processId /T /F 2>&1 | Out-Null
        return $true
    }
    catch {
        $message = $_.Exception.Message
        if ($message -match "not found|找不到|未找到|不存在") {
            return $true
        }
        Write-Warning "结束进程树失败：PID=$processId Message=$message"
        return $false
    }
}

function Quote-PowerShellSingle([string]$value) {
    return "'" + $value.Replace("'", "''") + "'"
}

function Start-LoggedDevProcess([string]$name, [string[]]$commandArgs, [string]$workingDirectory, [string]$logPath) {
    $scriptPath = Join-Path $env:TEMP ("agent-friend-dev-" + $name + "-" + [guid]::NewGuid().ToString("N") + ".ps1")
    $quotedCwd = Quote-PowerShellSingle $workingDirectory
    $quotedLog = Quote-PowerShellSingle $logPath
    # This helper is intentionally used only for fixed dev commands such as
    # `uv run python -m agent_bridge`; cmd.exe does the stderr merge before tee.
    $cmdLine = "(" + ($commandArgs -join " ") + ") 2>&1"
    $quotedCmdLine = Quote-PowerShellSingle $cmdLine
    $timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fffK")
    $script = @"
`$ErrorActionPreference = "Continue"
`$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(`$false)
Set-Location -LiteralPath $quotedCwd
"===== $timestamp $name start: $($commandArgs -join ' ') =====" | Tee-Object -FilePath $quotedLog -Append
& cmd.exe /d /s /c $quotedCmdLine | Tee-Object -FilePath $quotedLog -Append
`$exitCode = `$LASTEXITCODE
Remove-Item -LiteralPath `$PSCommandPath -Force -ErrorAction SilentlyContinue
exit `$exitCode
"@
    [System.IO.File]::WriteAllText($scriptPath, $script, [System.Text.UTF8Encoding]::new($true))
    return Start-Process -FilePath "powershell" -ArgumentList @("-NoProfile", "-File", $scriptPath) -PassThru -NoNewWindow
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

    Write-Host "  [WARN] 端口 $port 上已有 voice_bridge，${reason}；自动停止旧进程后重启…"
    foreach ($ownerPid in $owners) {
        Stop-ProcessTreeIfPresent $ownerPid | Out-Null
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

function Get-PortOwnerPids($port) {
    return @(
        Get-PortListeners $port |
            ForEach-Object { $_.OwningProcess } |
            Where-Object { $_ -gt 0 } |
            Sort-Object -Unique
    )
}

function Convert-ToComparableText([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        return ""
    }
    return $value.Replace("/", "\").TrimEnd("\").ToLowerInvariant()
}

$comparableRoot = Convert-ToComparableText $root
$devRuntimeProcessNames = @(
    "node.exe",
    "cmd.exe",
    "esbuild.exe",
    "rustup.exe",
    "cargo.exe",
    "app.exe",
    "msedgewebview2.exe"
)

function Test-TextMentionsRepo([string]$text) {
    $normalized = Convert-ToComparableText $text
    return $normalized -ne "" -and $normalized.Contains($comparableRoot)
}

function Get-ProcessSnapshot {
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.ProcessId -ne $PID })
    }
    catch {
        return @()
    }
}

function Find-ProcessInfo($processes, $processId) {
    foreach ($processInfo in $processes) {
        if ([int]$processInfo.ProcessId -eq [int]$processId) {
            return $processInfo
        }
    }
    return $null
}

function Test-DevRuntimeWrapperProcess($processInfo) {
    if (-not $processInfo) {
        return $false
    }
    $name = ([string]$processInfo.Name).ToLowerInvariant()
    return $devRuntimeProcessNames -contains $name
}

function Test-AgentFriendDevRuntimeSeed($processInfo) {
    if (-not (Test-DevRuntimeWrapperProcess $processInfo)) {
        return $false
    }
    if ((Test-TextMentionsRepo ([string]$processInfo.CommandLine)) -or
        (Test-TextMentionsRepo ([string]$processInfo.ExecutablePath))) {
        return $true
    }

    $name = ([string]$processInfo.Name).ToLowerInvariant()
    $commandLine = [string]$processInfo.CommandLine
    if ($name -eq "msedgewebview2.exe" -and
        $commandLine -match "--webview-exe-name=app\.exe" -and
        $commandLine -match "--remote-debugging-port=" -and
        $commandLine -match "com\.agentfriend\.desktop") {
        return $true
    }
    return $false
}

function Expand-DevRuntimeProcessIds($seedIds, $processes) {
    $selected = @{}
    foreach ($seedId in $seedIds) {
        if ([int]$seedId -gt 0) {
            $selected["$([int]$seedId)"] = $true
        }
    }

    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($processInfo in $processes) {
            $processId = [int]$processInfo.ProcessId
            $parentPid = [int]$processInfo.ParentProcessId
            $pidKey = "$processId"
            $parentKey = "$parentPid"

            if ($parentPid -gt 0 -and $selected.ContainsKey($parentKey) -and -not $selected.ContainsKey($pidKey)) {
                $selected[$pidKey] = $true
                $changed = $true
            }

            if ($selected.ContainsKey($pidKey) -and $parentPid -gt 0 -and -not $selected.ContainsKey($parentKey)) {
                $parent = Find-ProcessInfo $processes $parentPid
                if (Test-DevRuntimeWrapperProcess $parent) {
                    $selected[$parentKey] = $true
                    $changed = $true
                }
            }
        }
    }

    return @($selected.Keys | ForEach-Object { [int]$_ } | Sort-Object -Unique)
}

function Get-RootProcessIds($processIds, $processes) {
    $selected = @{}
    foreach ($processId in $processIds) {
        if ([int]$processId -gt 0) {
            $selected["$([int]$processId)"] = $true
        }
    }

    $roots = @()
    foreach ($processId in $processIds) {
        $processInfo = Find-ProcessInfo $processes $processId
        $parentPid = 0
        if ($processInfo) {
            $parentPid = [int]$processInfo.ParentProcessId
        }
        if ($parentPid -le 0 -or -not $selected.ContainsKey("$parentPid")) {
            $roots += [int]$processId
        }
    }
    return @($roots | Sort-Object -Unique)
}

function Format-ProcessSummary($processInfo) {
    if (-not $processInfo) {
        return "<unknown process>"
    }
    $commandLine = [string]$processInfo.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        $commandLine = [string]$processInfo.ExecutablePath
    }
    return "PID=$($processInfo.ProcessId) Name=$($processInfo.Name) CommandLine=$commandLine"
}

function Stop-AgentFriendDevRuntime($reason, [int[]]$requiredFreePorts, [switch]$failOnBlockedPort) {
    $processes = Get-ProcessSnapshot
    $seedIds = @(
        $processes |
            Where-Object { Test-AgentFriendDevRuntimeSeed $_ } |
            ForEach-Object { [int]$_.ProcessId }
    )
    $cleanupIds = @(Expand-DevRuntimeProcessIds $seedIds $processes)

    $blockingOwnerIds = @()
    foreach ($port in $requiredFreePorts) {
        $blockingOwnerIds += Get-PortOwnerPids $port
    }
    $blockingOwnerIds = @($blockingOwnerIds | Where-Object { $_ -gt 0 } | Sort-Object -Unique)

    $blockedByOther = @(
        $blockingOwnerIds |
            Where-Object { $cleanupIds -notcontains [int]$_ }
    )
    if ($failOnBlockedPort -and $blockedByOther.Count -gt 0) {
        foreach ($ownerPid in $blockedByOther) {
            $processInfo = Find-ProcessInfo $processes $ownerPid
            Write-Error "dev 端口被非本仓库进程占用，脚本不会自动结束它。$(Format-ProcessSummary $processInfo)"
        }
    }

    if ($cleanupIds.Count -gt 0) {
        $killRootIds = @(Get-RootProcessIds $cleanupIds $processes)
        $pidList = ($cleanupIds | Sort-Object -Unique) -join ","
        $rootPidList = ($killRootIds | Sort-Object -Unique) -join ","
        Write-Host "  [WARN] $reason；清理本仓库残留 dev 进程（PID=$pidList；root PID=$rootPidList）…"
        foreach ($cleanupPid in ($killRootIds | Sort-Object -Unique)) {
            Stop-ProcessTreeIfPresent $cleanupPid | Out-Null
        }

        for ($i = 0; $i -lt 30; $i++) {
            $stillBlocked = $false
            foreach ($port in $requiredFreePorts) {
                if (Test-PortInUse $port) {
                    $stillBlocked = $true
                    break
                }
            }
            if (-not $stillBlocked) {
                Write-Host "  [OK] dev 端口已释放"
                return $true
            }
            Start-Sleep -Milliseconds 100
        }
    }

    if ($failOnBlockedPort) {
        foreach ($port in $requiredFreePorts) {
            if (Test-PortInUse $port) {
                Write-Error "端口 $port 仍被占用，无法启动前端 dev server。请检查占用进程后重试。"
            }
        }
    }
    return $true
}

if ($mode -eq "tauri" -and $cdpEnabled) {
    if (Test-PortInUse $cdpPort) {
        Write-Error "WebView2 CDP 端口 $cdpPort 已被占用。请关闭占用进程，或改用 --cdp-port <port>，或传 --no-cdp。"
    }
}

if ($mode -eq "tauri" -and $cdpEnabled) {
    $flag = "--remote-debugging-port=$cdpPort"
    $currentWebViewArgs = $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
    if ($null -eq $currentWebViewArgs) {
        $currentWebViewArgs = ""
    }
    $cleanWebViewArgs = ($currentWebViewArgs -replace "(^|\s)--remote-debugging-port=\S+", "").Trim()
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "$cleanWebViewArgs $flag".Trim()
    Write-Host "===> WebView2 CDP: http://127.0.0.1:${cdpPort}（desktop-visual 可直接抓真实 WebView DOM / 截图）"
}
elseif ($mode -eq "tauri") {
    Write-Host "===> WebView2 CDP 已关闭（--no-cdp）"
}

uv run python (Join-Path $root "scripts\dev\check_bridge_processes.py")

$bridge = $null
$voice = $null
$cloudflared = $null

try {
if ($voiceMode -eq "tunnel" -and $mode -eq "web") {
    $tunnel = Start-VoiceTunnel
    $cloudflared = $tunnel.Process
}

if ($mode -eq "tauri") {
    # 047 · source desktop runtime ownership belongs to Tauri Core. The script
    # only prepares environment/tunnel state and launches the desktop shell.
    $env:AGENT_FRIEND_REPO_ROOT = $root
    $env:AGENT_FRIEND_DEV_VOICE_MODE = $voiceMode
    $env:AGENT_BRIDGE_DEV_MODE = "true"
    Remove-Item Env:VOICE_BRIDGE_PUBLIC_URL -ErrorAction SilentlyContinue
    $cloudflaredCommand = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cloudflaredCommand) {
        $env:AGENT_FRIEND_CLOUDFLARED_PATH = $cloudflaredCommand.Source
        Write-Host "===> cloudflared 由 Tauri Core 按通话需要启动：$($cloudflaredCommand.Source)"
    }
    else {
        Remove-Item Env:AGENT_FRIEND_CLOUDFLARED_PATH -ErrorAction SilentlyContinue
        if ($voiceMode -eq "tunnel") {
            Write-Error "--voice 需要 cloudflared；当前 PATH 中未找到。默认本地语音输入仍可用。"
        }
        Write-Host "  [WARN] 未找到 cloudflared；完整语音通话会给出 tunnel_binary_missing，文字与语音输入不受影响。"
    }
    Write-Host "===> 服务所有权：Tauri Core（bridge / voice_bridge）"
    Write-Host "===> 配置模式：${env:AGENT_FRIEND_CONFIG_MODE}（默认 desktop 不读取仓库 .env）"
    Set-Location (Join-Path $root "frontend")
    & uv run python $foregroundLogPath `
        --log $devTauriLog `
        --cwd (Join-Path $root "frontend") `
        -- node (Join-Path $root "frontend\node_modules\@tauri-apps\cli\tauri.js") dev @rest
    $frontendExitCode = $LASTEXITCODE
    if ($frontendExitCode -ne 0) {
        Write-Error "Tauri dev 命令退出，exit code=$frontendExitCode。已进入收尾清理。"
    }
    return
}

Write-Host "===> 启动 bridge（127.0.0.1:18800）…"
Write-Host "===> 配置模式：${env:AGENT_FRIEND_CONFIG_MODE}（默认 desktop 不读取仓库 .env）"
# AGENT_BRIDGE_DEV_MODE=true 挂 /dev/* 端点（如 /dev/fire-source 立即触发主动轮），
# 让 dev-fire-source / 状态机调试 / 018 push 通道真跑可用。生产环境永不开。
$env:AGENT_BRIDGE_DEV_MODE = "true"
$bridge = Start-LoggedDevProcess "bridge" @("uv", "run", "python", "-m", "agent_bridge") $root $devBridgeLog

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
        $voice = Start-LoggedDevProcess "voice" @("uv", "run", "python", "-m", "voice_bridge") $root $devVoiceLog
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
        $frontendExitCode = $LASTEXITCODE
    }
    else {
        Write-Host "===> 前端模式：tauri（桌面，含双窗口；首次编译 Rust 较慢）"
        & uv run python $foregroundLogPath `
            --log $devTauriLog `
            --cwd (Join-Path $root "frontend") `
            -- node (Join-Path $root "frontend\node_modules\@tauri-apps\cli\tauri.js") dev @rest
        $frontendExitCode = $LASTEXITCODE
    }
    if ($frontendExitCode -ne 0) {
        Write-Error "前端 dev 命令退出，exit code=$frontendExitCode。已进入收尾清理。"
    }
}
finally {
    Write-Host "`n===> 收尾：停止 bridge / voice_bridge / cloudflared…"
    if ($bridge -and -not $bridge.HasExited) {
        # /T 连同 uv 派生的 python 进程树一起结束
        Stop-ProcessTreeIfPresent $bridge.Id | Out-Null
    }
    if ($voice -and -not $voice.HasExited) {
        Stop-ProcessTreeIfPresent $voice.Id | Out-Null
    }
    if ($cloudflared -and -not $cloudflared.HasExited) {
        Stop-ProcessTreeIfPresent $cloudflared.Id | Out-Null
    }
    try {
        Stop-AgentFriendDevRuntime "脚本退出后检查前端/Tauri 残留" @()
    }
    catch {
        Write-Warning "清理前端/Tauri 残留失败：$($_.Exception.Message)"
    }
}
}
finally {
    if ($runtimeLeaseAcquired) {
        try {
            $releasePortsText = (Get-DevRuntimePorts) -join ","
            & uv run python $runtimeGuardPath release --worktree $root --mode $mode --ports $releasePortsText --owner-pid $PID | Out-Null
        }
        catch {
            Write-Warning "释放 dev runtime lease 失败：$($_.Exception.Message)"
        }
        $runtimeLeaseAcquired = $false
    }
    Set-Location $previousLocation
}
