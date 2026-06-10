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
  4. 检查 .env，没有则从 .env.example 复制
  5. 尽力装前端依赖（node 22+/pnpm 在则 pnpm install；缺则只提示、不报错）
  6. 检查 Rust/cargo（桌面端 Tauri 需要；缺则只提示）
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
Write-Host "  1. （首次）编辑 .env 填入 DEEPSEEK_API_KEY"
Write-Host "  2. 启动 CLI：      .\scripts\cli\run.ps1"
Write-Host "  3. 试用桌面端：    .\scripts\dev\run.ps1        （一键起 bridge + 桌面双窗口；--web 走浏览器）"
Write-Host "  4. 跑测试：        .\scripts\test\run.ps1"
Write-Host "  5. 一键全绿门禁：  .\scripts\check\run.ps1"
