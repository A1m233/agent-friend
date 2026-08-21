#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

exec uv run python scripts/deepseek-vision-smoke/run.py "$@"
