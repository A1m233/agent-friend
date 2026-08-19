# scripts/bridge/run.ps1 — 启动 agent-bridge HTTP SSE 服务（windows）
#
# 参数透传给 python -m agent_bridge。
# 默认不读取仓库 .env；存量流程可显式传 --legacy-dotenv。
# 默认监听 127.0.0.1:18800。详见 docs/requirements/006-agent-bridge/。
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "未检测到 uv。先跑 .\scripts\setup\run.ps1 初始化环境。"
}
$commandRuntimeOutput = @(& uv run python scripts/command-runtime/run.py ensure)
$commandRuntimeExitCode = $LASTEXITCODE
$codexExecutable = (($commandRuntimeOutput | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
if ($commandRuntimeExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($codexExecutable)) {
    Write-Error "命令运行时准备失败，Bridge 未启动：$codexExecutable"
}
$env:AGENT_FRIEND_CODEX_EXECUTABLE = $codexExecutable
uv run python -m agent_bridge @args
