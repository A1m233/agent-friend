# scripts/setup/run.ps1 — 一键环境初始化（windows）
# 详见 scripts/README.md
$ErrorActionPreference = "Stop"
# 强制 UTF-8 输出，避免中文在终端乱码（Windows PowerShell 5.1 默认按系统 ANSI/GBK 输出）
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

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

完整初始化流程（一键装全栈）：
  1. 检查 uv 是否已安装（未安装则提示安装命令并退出）
  2. （可选）--clean 时清理缓存
  3. 跑 uv sync 安装后端依赖
  4. 准备并校验固定版本的命令运行时
  5. 提示使用 scripts/credentials 配置本机系统凭据
  6. 尽力装前端依赖（node 22+/pnpm 在则 pnpm install；缺则只提示、不报错）
  7. 检查 Rust/cargo（桌面端 Tauri 需要；缺则只提示）
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
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "==> 准备固定命令运行时"
$commandRuntimeOutput = @(& uv run python scripts/command-runtime/run.py ensure)
$commandRuntimeExitCode = $LASTEXITCODE
$commandRuntimeMessage = (($commandRuntimeOutput | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
if ($commandRuntimeExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($commandRuntimeMessage)) {
    Write-Host "  [ERROR] 命令运行时准备失败：$commandRuntimeMessage"
    exit $(if ($commandRuntimeExitCode -ne 0) { $commandRuntimeExitCode } else { 1 })
}
Write-Host "  [OK] $commandRuntimeMessage"

Write-Host ""
Write-Host "==> 凭据配置"
Write-Host "  默认开发 / 桌面主路径使用系统凭据存储，不创建仓库 .env。"
Write-Host "  [TODO] 首次运行：.\scripts\credentials\run.ps1 set-bundle deepseek"
if (Test-Path .env) {
    Write-Host "  [INFO] 检测到已有 .env；仅在显式 --legacy-dotenv 时作为存量 fallback 使用。"
}

# 前端依赖 best-effort：工具齐就装，缺工具只提示、不让整个 setup 失败（后端已就绪）。
Write-Host ""
Write-Host "==> 前端依赖（best-effort）"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "  [SKIP] 未检测到 node。桌面前端需 Node.js 22+（建议 nvm-windows / fnm）。装好后跑：.\scripts\frontend\install.ps1"
} elseif ([int](((node -v) -replace 'v','') -split '\.')[0] -lt 22) {
    Write-Host "  [SKIP] Node 版本过低（当前 $(node -v)），需要 22+。升级后跑：.\scripts\frontend\install.ps1"
} elseif (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host "  [SKIP] 未检测到 pnpm。启用（一次性）：corepack enable pnpm，然后跑：.\scripts\frontend\install.ps1"
} else {
    Write-Host "  node $(node -v) / pnpm $(pnpm -v)"
    Push-Location frontend
    pnpm install
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { exit $code }
}

Write-Host ""
Write-Host "==> 检查 Rust（桌面端 Tauri 需要）"
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    $cargoVersion = (cargo --version) -split ' ' | Select-Object -Index 1
    Write-Host "  cargo $cargoVersion"
} else {
    Write-Host "  [SKIP] 未检测到 Rust/cargo。仅 web 调试（.\scripts\frontend\web.ps1）不需要；"
    Write-Host "         要跑桌面端（.\scripts\dev\run.ps1）请装：https://rustup.rs"
}

Write-Host ""
Write-Host "==> 完成！接下来："
Write-Host "  1. （首次）配置凭据：.\scripts\credentials\run.ps1 set-bundle deepseek"
Write-Host "  2. 启动 CLI：      .\scripts\cli\run.ps1"
Write-Host "  3. 试用桌面端：    .\scripts\dev\run.ps1        （一键起 bridge + 桌面双窗口；--web 走浏览器）"
Write-Host "  4. 跑测试：        .\scripts\test\run.ps1"
Write-Host "  5. 一键全绿门禁：  .\scripts\check\run.ps1"
