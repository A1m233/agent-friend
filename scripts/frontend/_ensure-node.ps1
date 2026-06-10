# scripts/frontend/_ensure-node.ps1 — node/pnpm 版本守卫
# 注意：本文件供其它 frontend 脚本 dot-source，不直接执行。详见 scripts/README.md
function Ensure-NodePnpm {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Host "  [ERROR] 未检测到 node。请安装 Node.js 22+（建议用 nvm-windows / fnm 等版本管理器）。"
        exit 1
    }
    $major = [int](((node -v) -replace 'v','') -split '\.')[0]
    if ($major -lt 22) {
        Write-Host "  [ERROR] Node 版本过低（当前 $(node -v)），需要 22+。"
        Write-Host "          用 nvm-windows 的话：先  nvm use 22  ，再重试。"
        exit 1
    }
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        Write-Host "  [ERROR] 未检测到 pnpm。启用方式（一次性）：corepack enable pnpm"
        exit 1
    }
}
