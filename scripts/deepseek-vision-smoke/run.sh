#!/usr/bin/env bash
# 151: three synthetic tool-image requests; no Files API uploads.
set -euo pipefail
cd "$(dirname "$0")/../.."

exec uv run python scripts/deepseek-vision-smoke/run.py "$@"
