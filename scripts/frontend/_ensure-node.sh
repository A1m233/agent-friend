#!/usr/bin/env bash
# scripts/frontend/_ensure-node.sh — node/pnpm 版本守卫
# 注意：本文件供其它 frontend 脚本 `source`，不直接执行。详见 scripts/README.md
ensure_node_pnpm() {
    if ! command -v node >/dev/null 2>&1; then
        echo "  [ERROR] 未检测到 node。请安装 Node.js 22+（建议用 nvm / fnm 等版本管理器）。" >&2
        return 1
    fi
    local major
    major="$(node -v | sed 's/v\([0-9]*\).*/\1/')"
    if [ "$major" -lt 22 ]; then
        echo "  [ERROR] Node 版本过低（当前 $(node -v)），需要 22+。" >&2
        echo "          用 nvm 的话：先跑  nvm use 22  （已设为默认，新开终端会自动生效），再重试。" >&2
        return 1
    fi
    if ! command -v pnpm >/dev/null 2>&1; then
        echo "  [ERROR] 未检测到 pnpm。启用方式（一次性）：corepack enable pnpm" >&2
        return 1
    fi
}
