#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# --verify-checkout scans without writes; --sync-checkout requires a clean checkout.

if command -v uv >/dev/null 2>&1; then
  exec uv run --no-project --offline --no-python-downloads python scripts/showcase-snapshot/snapshot.py "$@"
fi

exec python3 scripts/showcase-snapshot/snapshot.py "$@"
