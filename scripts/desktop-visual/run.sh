#!/usr/bin/env bash
# scripts/desktop-visual/run.sh - desktop visual/DOM inspection harness (mac/linux).
# pair-capture remains Windows-only because macOS does not use WebView2.
# See scripts/README.md.
set -euo pipefail
cd "$(dirname "$0")/../.."

if ! command -v uv >/dev/null 2>&1; then
    echo "  [ERROR] uv is required. Run ./scripts/setup/run.sh first." >&2
    exit 1
fi

exec uv run python scripts/desktop-visual/run.py "$@"
