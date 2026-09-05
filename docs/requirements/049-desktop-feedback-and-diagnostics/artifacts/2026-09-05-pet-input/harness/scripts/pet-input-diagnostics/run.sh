#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

if ! command -v node >/dev/null 2>&1; then
    echo "Node 22+ is required. Run ./scripts/setup/run.sh first." >&2
    exit 1
fi

exec node scripts/pet-input-diagnostics/run.mjs "$@"
