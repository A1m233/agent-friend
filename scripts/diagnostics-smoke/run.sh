#!/usr/bin/env bash
# --native adds production Rust collection/archive measurements; --output saves JSON.
# --desktop-action focus checks layout stability across real focus-triggered metadata refreshes.
# --desktop-action layout checks the compact export flow and keyboard disclosure in the live desktop.
# --desktop-seed creates isolated synthetic data; --desktop-check inspects an already running Tauri WebView.
# --desktop-augment adds large materials; --desktop-verify checks resulting ZIPs and records sanitized evidence.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python scripts/diagnostics-smoke/run.py "$@"
