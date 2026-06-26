#!/usr/bin/env bash
# scripts/dev/run.sh — 一键起 bridge + 前端开发（mac/linux）
#
# 默认：Tauri 桌面（含桌宠 / 对话双窗口，首次编译 Rust 较慢），并启动 voice_bridge。
# --web：改为浏览器 web 模式（仅 vite，不编译 Rust，热更快、但跑不出桌宠透明窗 / 多窗口）。
# --no-voice：不启动 voice_bridge（轻量前端 / 桌宠开发）。
# 其余参数透传给前端命令（pnpm tauri dev / pnpm dev）。
# bridge 默认听 127.0.0.1:18800；voice_bridge 默认听 127.0.0.1:18900；前端经 vite proxy 连它们。
# 输出分别加 [bridge] / [voice] / [app|web] 前缀；Ctrl+C 一起退（清理整个进程组）。
# 详见 scripts/README.md
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

MODE="tauri"
START_VOICE=1
CONFIG_MODE="${AGENT_FRIEND_CONFIG_MODE:-desktop}"
ARGS=()
for a in "$@"; do
    case "$a" in
        --web) MODE="web" ;;
        --tauri) MODE="tauri" ;;
        --voice) START_VOICE=1 ;;
        --no-voice) START_VOICE=0 ;;
        --legacy-dotenv) CONFIG_MODE="legacy-dotenv" ;;
        --config-mode=*) CONFIG_MODE="${a#--config-mode=}" ;;
        *) ARGS+=("$a") ;;
    esac
done
export AGENT_FRIEND_CONFIG_MODE="$CONFIG_MODE"

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

http_ok() {
    local url="$1"
    command -v curl >/dev/null 2>&1 && curl -fsS --max-time 2 "$url" >/dev/null 2>&1
}

wait_http_ok() {
    local url="$1"
    local attempts="$2"
    local i=0
    while [ "$i" -lt "$attempts" ]; do
        if http_ok "$url"; then
            return 0
        fi
        sleep 0.2
        i=$((i + 1))
    done
    return 1
}

port_in_use() {
    local port="$1"
    command -v lsof >/dev/null 2>&1 && lsof -ti tcp:"$port" >/dev/null 2>&1
}

wait_cloudflared_url() {
    local out_log="$1"
    local err_log="$2"
    local timeout_seconds="$3"
    local deadline=$((SECONDS + timeout_seconds))
    local public_url=""
    while [ "$SECONDS" -lt "$deadline" ]; do
        public_url=$(grep -Eho 'https://[a-z0-9-]+\.trycloudflare\.com' "$out_log" "$err_log" 2>/dev/null | head -n 1 || true)
        if [ -n "$public_url" ]; then
            printf '%s\n' "$public_url"
            return 0
        fi
        sleep 0.5
    done
    return 1
}

start_voice_tunnel() {
    if [ -n "${VOICE_BRIDGE_PUBLIC_URL:-}" ]; then
        echo "===> 使用当前进程 VOICE_BRIDGE_PUBLIC_URL（不读取 .env）"
        return 0
    fi

    if ! command -v cloudflared >/dev/null 2>&1; then
        echo "  [ERROR] 未检测到 cloudflared。语音通话需要公网回调 URL；请安装 cloudflared，或在当前 shell 显式设置 VOICE_BRIDGE_PUBLIC_URL 后重试。" >&2
        exit 1
    fi

    local out_log="${TMPDIR:-/tmp}/agent-friend-cloudflared-dev.log"
    local err_log="${TMPDIR:-/tmp}/agent-friend-cloudflared-dev.err.log"
    : > "$out_log"
    : > "$err_log"

    echo "===> 启动 cloudflared tunnel（127.0.0.1:18900）…"
    cloudflared tunnel --url "http://127.0.0.1:18900" >"$out_log" 2>"$err_log" &
    local tunnel_pid=$!

    local public_url
    if ! public_url=$(wait_cloudflared_url "$out_log" "$err_log" 60); then
        kill "$tunnel_pid" 2>/dev/null || true
        echo "  [ERROR] cloudflared 60 秒内没有输出 trycloudflare URL。日志：$out_log / $err_log" >&2
        exit 1
    fi

    export VOICE_BRIDGE_PUBLIC_URL="$public_url"
    echo "  [OK] VOICE_BRIDGE_PUBLIC_URL=$public_url"
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
    echo "===> 收尾：停止 bridge / voice_bridge / 前端…"
    kill 0 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "===> 启动 bridge（127.0.0.1:18800）…"
echo "===> 配置模式：$AGENT_FRIEND_CONFIG_MODE（默认 desktop 不读取仓库 .env）"
# AGENT_BRIDGE_DEV_MODE=true 挂 /dev/* 端点（如 /dev/fire-source 立即触发主动轮），
# 让 dev-fire-source / 状态机调试 / 018 push 通道真跑可用。生产环境永不开。
# 用 export 而非 inline `env=val cmd`：subshell + background + pipe 组合下 inline 形式
# 偶发不把 env 传到 child process；export 让 env 在脚本全局生效、subshell 自动继承。
export AGENT_BRIDGE_DEV_MODE=true
(cd "$ROOT" && uv run python -m agent_bridge) 2>&1 | prefix "[bridge] " &

if [ "$START_VOICE" = "1" ]; then
    if http_ok "http://127.0.0.1:18900/healthz"; then
        if [ -z "${VOICE_BRIDGE_PUBLIC_URL:-}" ]; then
            echo "  [ERROR] voice_bridge 已在 127.0.0.1:18900 运行，但 dev/run 无法给既有进程注入本次 tunnel URL。请先停止旧 voice_bridge，再重新运行 dev/run；或在当前 shell 显式设置 VOICE_BRIDGE_PUBLIC_URL 后复用。" >&2
            exit 1
        fi
        echo "===> voice_bridge 已在 127.0.0.1:18900 运行，复用现有进程（VOICE_BRIDGE_PUBLIC_URL 来自当前环境；不读取 .env）"
    elif port_in_use 18900; then
        echo "  [ERROR] 端口 18900 已被占用，但 http://127.0.0.1:18900/healthz 不可用。请先停止占用该端口的进程后再试。" >&2
        exit 1
    else
        start_voice_tunnel
        echo "===> 启动 voice_bridge（127.0.0.1:18900）…"
        (cd "$ROOT" && uv run python -m voice_bridge) 2>&1 | prefix "[voice] " &
        if ! wait_http_ok "http://127.0.0.1:18900/healthz" 50; then
            echo "  [ERROR] voice_bridge 启动后 10 秒内未通过健康检查。请查看上方 [voice] 日志。" >&2
            exit 1
        fi
    fi
else
    echo "===> 跳过 voice_bridge（--no-voice）"
fi

if [ "$MODE" = "web" ]; then
    echo "===> 前端模式：web（浏览器调试，不编译 Rust）"
    (cd "$ROOT/frontend" && pnpm dev ${ARGS[@]+"${ARGS[@]}"}) 2>&1 | prefix "[web] " &
else
    echo "===> 前端模式：tauri（桌面，含双窗口；首次编译 Rust 较慢）"
    (cd "$ROOT/frontend" && pnpm tauri dev ${ARGS[@]+"${ARGS[@]}"}) 2>&1 | prefix "[app] " &
fi

if [ "$START_VOICE" = "1" ]; then
    echo "===> 已拉起 bridge + voice_bridge + 前端，Ctrl+C 一起退"
else
    echo "===> 已拉起 bridge + 前端，Ctrl+C 一起退"
fi
wait
