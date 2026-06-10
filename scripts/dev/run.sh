#!/usr/bin/env bash
# scripts/dev/run.sh — 一键起 bridge + 前端开发（mac/linux）
#
# 默认：Tauri 桌面（含桌宠 / 对话双窗口，首次编译 Rust 较慢）。
# --web：改为浏览器 web 模式（仅 vite，不编译 Rust，热更快、但跑不出桌宠透明窗 / 多窗口）。
# 其余参数透传给前端命令（pnpm tauri dev / pnpm dev）。
# bridge 默认听 127.0.0.1:18800；前端经 vite proxy 连它（见 frontend/vite.config.ts）。
# 两路输出分别加 [bridge] / [app|web] 前缀；Ctrl+C 一起退（清理整个进程组）。
# 详见 scripts/README.md
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

MODE="tauri"
ARGS=()
for a in "$@"; do
    case "$a" in
        --web) MODE="web" ;;
        --tauri) MODE="tauri" ;;
        *) ARGS+=("$a") ;;
    esac
done

if ! command -v uv >/dev/null 2>&1; then
    echo "  [ERROR] 未检测到 uv。先跑 ./scripts/setup/run.sh 初始化环境。" >&2
    exit 1
fi
# shellcheck source=/dev/null
. "$ROOT/scripts/frontend/_ensure-node.sh"
ensure_node_pnpm

# 给每行输出加前缀（避免 BSD sed 无 -u 的差异，用便携的 while-read）。
prefix() {
    local p="$1"
    while IFS= read -r line; do printf '%s%s\n' "$p" "$line"; done
}

cleanup() {
    trap - INT TERM EXIT
    echo ""
    echo "===> 收尾：停止 bridge + 前端…"
    kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "===> 启动 bridge（127.0.0.1:18800）…"
(cd "$ROOT" && uv run python -m agent_bridge) 2>&1 | prefix "[bridge] " &

if [ "$MODE" = "web" ]; then
    echo "===> 前端模式：web（浏览器调试，不编译 Rust）"
    (cd "$ROOT/frontend" && pnpm dev ${ARGS[@]+"${ARGS[@]}"}) 2>&1 | prefix "[web] " &
else
    echo "===> 前端模式：tauri（桌面，含双窗口；首次编译 Rust 较慢）"
    (cd "$ROOT/frontend" && pnpm tauri dev ${ARGS[@]+"${ARGS[@]}"}) 2>&1 | prefix "[app] " &
fi

echo "===> 已拉起 bridge + 前端，Ctrl+C 一起退"
wait
