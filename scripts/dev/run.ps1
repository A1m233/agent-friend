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
