#!/usr/bin/env bash
# Requires an already-running Tauri dev runtime with Chromium CDP (normally Windows/WebView2).
# macOS WKWebView without a CDP endpoint is unsupported; this script does not start any runtime.
# inspect-animation reads two existing ticker/canvas samples without changing the app or saving images.
# Script-execution overrides can lose pending rAF callbacks and are intentionally unsupported.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/frontend/_ensure-node.sh
ensure_node_pnpm
exec node scripts/pet-input-evidence-smoke/run.mjs "$@"
