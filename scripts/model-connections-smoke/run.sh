#!/usr/bin/env bash
# 151: capabilities uses tool images; documents only check reference projection.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ "${1:-}" == "cpa" ]]; then
  shift
  exec uv run python scripts/model-connections-smoke/cpa.py "$@"
fi
if [[ "${1:-}" == "inspect" ]]; then
  shift
  exec uv run python scripts/model-connections-smoke/inspection_smoke.py "$@"
fi
if [[ "${1:-}" == "capabilities" ]]; then
  shift
  exec uv run python scripts/model-connections-smoke/capability_smoke.py "$@"
fi
exec uv run python scripts/model-connections-smoke/run.py "$@"

# --custom: generic connection protocol; bounded synthetic compaction trigger, 4096 output tokens.
