# scripts/rust-check/run.ps1 — Tauri Core 测试与 release 编译门禁（windows）
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Host "[error] 未找到 cargo，请先安装 Rust 工具链（https://rustup.rs/）"
    exit 1
}

Write-Host "===> cargo fmt --check"
cargo fmt --check --manifest-path frontend/src-tauri/Cargo.toml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "===> cargo test"
cargo test --manifest-path frontend/src-tauri/Cargo.toml @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "===> cargo check --release"
cargo check --release --manifest-path frontend/src-tauri/Cargo.toml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
