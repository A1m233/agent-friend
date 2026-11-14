#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -X utf8 scripts/notification-smoke/run.py "$@"
