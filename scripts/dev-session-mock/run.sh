#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ "${1:-}" == "streaming" && -n "${2:-}" ]]; then
  exec pnpm --dir frontend exec node ../scripts/dev-session-mock/control.mjs "$2"
fi
exec uv run python scripts/dev-session-mock/run.py "$@"
