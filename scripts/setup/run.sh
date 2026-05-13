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

完整初始化流程：
  1. 检查 uv 是否已安装（未安装则提示安装命令并退出）
  2. （可选）--clean 时清理缓存
  3. 跑 uv sync 安装依赖
  4. 检查 .env，没有则从 .env.example 复制
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

echo
echo "==> 完成！接下来："
echo "  1. （首次）编辑 .env 填入 DEEPSEEK_API_KEY"
echo "  2. 启动 CLI：    ./scripts/cli/run.sh"
echo "  3. 跑测试：      ./scripts/test/run.sh"
echo "  4. lint 检查：   ./scripts/lint/run.sh"
echo "  5. 自动修复：    ./scripts/fix/run.sh"
echo "  6. 类型检查：    ./scripts/typecheck/run.sh"
