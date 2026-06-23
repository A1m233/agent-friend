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

# 028 · dev 脚本启动前端前，若 1420 被上次残留进程占用，尝试自动清理。
# 不自动换端口：1420/1421 与 tauri.conf.json / vite.config.ts 多处约定耦合，换端口更易出错。
DEV_PORT=1420

cleanup_port() {
    local port="$1"
    if ! command -v lsof >/dev/null 2>&1; then
        return 1
    fi
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -z "$pids" ]; then
        return 0
    fi
    echo "  [WARN] 端口 $port 被占用，尝试清理上次残留的 vite/tauri 进程…"
    local killed=()
    for pid in $pids; do
        local comm
        comm=$(ps -p "$pid" -o comm= 2>/dev/null || true)
        # 只杀疑似前端 dev 相关的进程，避免误伤用户其他服务。
        case "$comm" in
            node|pnpm|tauri|cargo|"app"*)
                if kill -TERM "$pid" 2>/dev/null; then
                    killed+=("$pid")
                fi
                ;;
        esac
    done
    # 等最多 2 秒让进程退出
    for _ in {1..20}; do
        if ! lsof -ti tcp:"$port" >/dev/null 2>&1; then
            echo "  [OK] 端口 $port 已释放"
            return 0
        fi
        sleep 0.1
    done
    # 还有残留则强制杀
    local remaining
    remaining=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    for pid in $remaining; do
        kill -KILL "$pid" 2>/dev/null || true
    done
    sleep 0.2
    if ! lsof -ti tcp:"$port" >/dev/null 2>&1; then
        echo "  [OK] 端口 $port 已释放"
        return 0
    fi
    return 1
}

if ! cleanup_port "$DEV_PORT"; then
    echo "  [ERROR] 端口 $DEV_PORT 仍被占用，无法启动前端 dev server。" >&2
    echo "          请检查是否有其他程序占用该端口，或手动终止相关进程后再试。" >&2
    exit 1
fi

cleanup() {
    trap - INT TERM EXIT
    echo ""
    echo "===> 收尾：停止 bridge + 前端…"
    kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "===> 启动 bridge（127.0.0.1:18800）…"
# AGENT_BRIDGE_DEV_MODE=true 挂 /dev/* 端点（如 /dev/fire-source 立即触发主动轮），
# 让 dev-fire-source / 状态机调试 / 018 push 通道真跑可用。生产环境永不开。
# 用 export 而非 inline `env=val cmd`：subshell + background + pipe 组合下 inline 形式
# 偶发不把 env 传到 child process；export 让 env 在脚本全局生效、subshell 自动继承。
export AGENT_BRIDGE_DEV_MODE=true
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
