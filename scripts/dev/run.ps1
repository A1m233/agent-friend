# scripts/dev/run.ps1 — 一键起 bridge + 前端开发（windows）
#
# 默认：Tauri 桌面（含桌宠 / 对话双窗口，首次编译 Rust 较慢）。
# --web：改为浏览器 web 模式（仅 vite，不编译 Rust，热更快、但跑不出桌宠透明窗 / 多窗口）。
# 其余参数透传给前端命令（pnpm tauri dev / pnpm dev）。
# bridge 默认听 127.0.0.1:18800；前端经 vite proxy 连它（见 frontend/vite.config.ts）。
# bridge 与前端共用同一控制台输出（不加前缀，与 sh 端的小差异）；Ctrl+C / 退出时 taskkill /T 清理 bridge 进程树。
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

$mode = "tauri"
$rest = @()
foreach ($a in $args) {
    switch ($a) {
        "--web" { $mode = "web" }
        "--tauri" { $mode = "tauri" }
        default { $rest += $a }
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 uv。先跑 .\scripts\setup\run.ps1 初始化环境。"
    exit 1
}
. (Join-Path $PSScriptRoot "..\frontend\_ensure-node.ps1")
Ensure-NodePnpm

# 028 · dev 脚本启动前端前，若 1420 被上次残留进程占用，尝试自动清理。
# 不自动换端口：1420/1421 与 tauri.conf.json / vite.config.ts 多处约定耦合，换端口更易出错。
$DevPort = 1420

function Test-PortInUse($port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction Stop
        return $conn.Count -gt 0
    }
    catch {
        return $false
    }
}

function Stop-DevPortProcess($port) {
    $killed = @()
    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        # 只杀疑似前端 dev 相关的进程，避免误伤用户其他服务。
        $name = $proc.ProcessName
        if ($name -in @("node", "pnpm", "tauri", "cargo", "app")) {
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                $killed += $proc.Id
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

if (Test-PortInUse $DevPort) {
    Write-Host "  [WARN] 端口 $DevPort 被占用，尝试清理上次残留的 vite/tauri 进程…"
    if (Stop-DevPortProcess $DevPort) {
        Write-Host "  [OK] 端口 $DevPort 已释放"
    }
    else {
        Write-Error "端口 $DevPort 仍被占用，无法启动前端 dev server。请检查是否有其他程序占用该端口，或手动终止相关进程后再试。"
        exit 1
    }
}

Write-Host "===> 启动 bridge（127.0.0.1:18800）…"
# AGENT_BRIDGE_DEV_MODE=true 挂 /dev/* 端点（如 /dev/fire-source 立即触发主动轮），
# 让 dev-fire-source / 状态机调试 / 018 push 通道真跑可用。生产环境永不开。
$env:AGENT_BRIDGE_DEV_MODE = "true"
$bridge = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "agent_bridge") -PassThru -NoNewWindow

try {
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
    Write-Host "`n===> 收尾：停止 bridge…"
    if ($bridge -and -not $bridge.HasExited) {
        # /T 连同 uv 派生的 python 进程树一起结束
        taskkill /PID $bridge.Id /T /F 2>$null | Out-Null
    }
}
