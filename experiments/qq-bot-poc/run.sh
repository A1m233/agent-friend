#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "→ 缺少 .env，先执行：cp .env.example .env"
    echo "  并填入 QQ_BOT_APPID / QQ_BOT_SECRET"
    exit 1
fi

echo "→ uv sync（同步依赖）"
uv sync

echo "→ 启动 echo bot（Ctrl+C 退出）"
echo
exec uv run python src/echo_bot.py
