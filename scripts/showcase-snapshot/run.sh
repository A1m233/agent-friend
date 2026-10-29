#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# --verify-checkout scans without writes; --sync-checkout requires a clean checkout.
# Optional SHOWCASE_PRIVATE_USER / SHOWCASE_PRIVATE_DISPLAY_NAME configure personal redaction.
# Snapshots retain LICENSE, NOTICE, THIRD_PARTY_NOTICES.md and licenses/.

if command -v uv >/dev/null 2>&1; then
  exec uv run --no-project --offline --no-python-downloads python scripts/showcase-snapshot/snapshot.py "$@"
fi

exec python3 scripts/showcase-snapshot/snapshot.py "$@"
