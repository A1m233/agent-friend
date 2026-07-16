#!/usr/bin/env bash
# scripts/rust-check/run.sh — Tauri Core 测试与 release 编译门禁（mac/linux）
set -euo pipefail
cd "$(dirname "$0")/../.."

if ! command -v cargo >/dev/null 2>&1; then
    echo "[error] 未找到 cargo，请先安装 Rust 工具链（https://rustup.rs/）"
    exit 1
fi

echo "===> cargo fmt --check"
cargo fmt --check --manifest-path frontend/src-tauri/Cargo.toml
echo "===> cargo test"
cargo test --manifest-path frontend/src-tauri/Cargo.toml "$@"
echo "===> cargo check --release"
cargo check --release --manifest-path frontend/src-tauri/Cargo.toml
