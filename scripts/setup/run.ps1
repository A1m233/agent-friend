# scripts/setup/run.ps1 — 一键环境初始化（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

$Clean = $false
foreach ($arg in $args) {
    switch ($arg) {
        '--clean' { $Clean = $true }
        { $_ -in '-h', '--help' } {
            @"
Usage: scripts/setup/run.ps1 [--clean]

  --clean   清理 .venv / __pycache__ / .pytest_cache / .mypy_cache / .ruff_cache 后再初始化

完整初始化流程：
  1. 检查 uv 是否已安装（未安装则提示安装命令并退出）
  2. （可选）--clean 时清理缓存
  3. 跑 uv sync 安装依赖
  4. 检查 .env，没有则从 .env.example 复制
"@ | Write-Host
            exit 0
        }
        default {
            Write-Host "未知参数：$arg"
            Write-Host "用 -h 查看帮助。"
            exit 1
        }
    }
}

Write-Host "==> agent-friend 环境初始化"
Write-Host "工作目录：$RepoRoot"
Write-Host ""

Write-Host "==> 检查 uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  [ERROR] 未检测到 uv。"
    Write-Host ""
    Write-Host "  请先安装 uv（一次性）："
    Write-Host '    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Write-Host ""
    Write-Host "  安装完成后重新打开终端，再次运行本脚本。"
    exit 1
}
$uvVersion = (uv --version) -split ' ' | Select-Object -Index 1
Write-Host "  uv $uvVersion"

if ($Clean) {
    Write-Host ""
    Write-Host "==> 清理缓存（--clean）"
    if (Test-Path .venv) { Remove-Item .venv -Recurse -Force }
    Get-ChildItem -Recurse -Directory -Filter '__pycache__'   -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Directory -Filter '.pytest_cache' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Directory -Filter '.mypy_cache'   -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Directory -Filter '.ruff_cache'   -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  清理完成"
}

Write-Host ""
Write-Host "==> 安装依赖（uv sync）"
uv sync

Write-Host ""
Write-Host "==> 检查 .env"
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "  已从 .env.example 创建 .env"
    Write-Host "  [TODO] 请编辑 .env 填入 DEEPSEEK_API_KEY"
} else {
    Write-Host "  .env 已存在，跳过"
}

Write-Host ""
Write-Host "==> 完成！接下来："
Write-Host "  1. （首次）编辑 .env 填入 DEEPSEEK_API_KEY"
Write-Host "  2. 启动 CLI：    .\scripts\cli\run.ps1"
Write-Host "  3. 跑测试：      .\scripts\test\run.ps1"
Write-Host "  4. lint 检查：   .\scripts\lint\run.ps1"
Write-Host "  5. 自动修复：    .\scripts\fix\run.ps1"
Write-Host "  6. 类型检查：    .\scripts\typecheck\run.ps1"
