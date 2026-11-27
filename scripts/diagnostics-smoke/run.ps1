$ErrorActionPreference = "Stop"
# --native adds production Rust collection/archive measurements; --output saves JSON.
# --desktop-action focus checks layout stability across real focus-triggered metadata refreshes.
# --desktop-action layout checks the compact export flow and keyboard disclosure in the live desktop.
# --desktop-seed creates isolated synthetic data; --desktop-check inspects an already running Tauri WebView.
# --desktop-augment adds large materials; --desktop-verify checks resulting ZIPs and records sanitized evidence.
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Set-Location (Join-Path $PSScriptRoot "..\..")
& uv run python scripts/diagnostics-smoke/run.py @args
exit $LASTEXITCODE
