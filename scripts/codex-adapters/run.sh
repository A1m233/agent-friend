#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

mode="${1:-doctor}"
if [[ "$mode" == "sync-builtin" || "$mode" == "verify-builtin" ]]; then
  exec uv run python scripts/codex-adapters/builtin_skill.py "$@"
fi
if [[ "$mode" != "sync" && "$mode" != "doctor" ]]; then
  echo "Usage: ./scripts/codex-adapters/run.sh [sync|doctor|sync-builtin|verify-builtin]" >&2
  exit 2
fi
shift || true

if [[ "$mode" == "sync" ]]; then
  exec "$HOME/.cursor/scripts/codex-adapters.py" project-sync --root . "$@"
fi

exec "$HOME/.cursor/scripts/codex-adapters.py" project-doctor --root . "$@"
