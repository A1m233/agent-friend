#!/usr/bin/env bash
# scripts/setup/run.sh — 一键环境初始化（mac/linux）
# 详见 scripts/README.md
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

CLEAN=false
for arg in "$@"; do
    case "$arg" in
        --clean)
            CLEAN=true
            ;;
        -h|--help)
            cat <<'EOF'
Usage: scripts/setup/run.sh [--clean]

  --clean   清理 .venv / __pycache__ / .pytest_cache / .mypy_cache / .ruff_cache 后再初始化

完整初始化流程（一键装全栈）：
  1. 检查 uv 是否已安装（未安装则提示安装命令并退出）
  2. （可选）--clean 时清理缓存
  3. 跑 uv sync 安装后端依赖
  4. 检查 .env，没有则从 .env.example 复制
  5. 尽力装前端依赖（node 22+/pnpm 在则 pnpm install；缺则只提示、不报错）
  6. 检查 Rust/cargo（桌面端 Tauri 需要；缺则只提示）
EOF
            exit 0
            ;;
        *)
            echo "未知参数：$arg"
            echo "用 -h 查看帮助。"
            exit 1
            ;;
    esac
done

echo "==> agent-friend 环境初始化"
echo "工作目录：$REPO_ROOT"
echo

echo "==> 检查 uv"
if ! command -v uv >/dev/null 2>&1; then
    echo "  [ERROR] 未检测到 uv。"
    echo
    echo "  请先安装 uv（一次性）："
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo
    echo "  安装完成后重新打开终端，再次运行本脚本。"
    exit 1
fi
echo "  uv $(uv --version | awk '{print $2}')"

if [ "$CLEAN" = true ]; then
    echo
    echo "==> 清理缓存（--clean）"
    rm -rf .venv
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
    echo "  清理完成"
fi

echo
echo "==> 安装依赖（uv sync）"
uv sync

echo
echo "==> 检查 .env"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  已从 .env.example 创建 .env"
    echo "  [TODO] 请编辑 .env 填入 DEEPSEEK_API_KEY"
else
    echo "  .env 已存在，跳过"
fi

# 前端依赖 best-effort：工具齐就装，缺工具只提示、不让整个 setup 失败（后端已就绪）。
echo
echo "==> 前端依赖（best-effort）"
if ! command -v node >/dev/null 2>&1; then
    echo "  [SKIP] 未检测到 node。桌面前端需 Node.js 22+（建议 nvm / fnm）。装好后跑：./scripts/frontend/install.sh"
elif [ "$(node -v | sed 's/v\([0-9]*\).*/\1/')" -lt 22 ]; then
    echo "  [SKIP] Node 版本过低（当前 $(node -v)），需要 22+。升级后跑：./scripts/frontend/install.sh"
elif ! command -v pnpm >/dev/null 2>&1; then
    echo "  [SKIP] 未检测到 pnpm。启用（一次性）：corepack enable pnpm，然后跑：./scripts/frontend/install.sh"
else
    echo "  node $(node -v) / pnpm $(pnpm -v)"
    ( cd frontend && pnpm install )
fi

echo
echo "==> 检查 Rust（桌面端 Tauri 需要）"
if command -v cargo >/dev/null 2>&1; then
    echo "  cargo $(cargo --version | awk '{print $2}')"
else
    echo "  [SKIP] 未检测到 Rust/cargo。仅 web 调试（./scripts/frontend/web.sh）不需要；"
    echo "         要跑桌面端（./scripts/dev/run.sh）请装：https://rustup.rs"
fi

echo
echo "==> 完成！接下来："
echo "  1. （首次）编辑 .env 填入 DEEPSEEK_API_KEY"
echo "  2. 启动 CLI：      ./scripts/cli/run.sh"
echo "  3. 试用桌面端：    ./scripts/dev/run.sh        （一键起 bridge + 桌面双窗口；--web 走浏览器）"
echo "  4. 跑测试：        ./scripts/test/run.sh"
echo "  5. 一键全绿门禁：  ./scripts/check/run.sh"
