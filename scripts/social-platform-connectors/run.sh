#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

if [[ "${1:-}" == "agent-smoke" ]]; then
    shift
    exec uv run python scripts/social-platform-connectors/agent_smoke.py "$@"
fi

if [[ "${1:-}" == "quality" ]]; then
    shift
    exec uv run python scripts/social-platform-connectors/quality.py "$@"
fi

exec uv run python scripts/social-platform-connectors/run.py "$@"
