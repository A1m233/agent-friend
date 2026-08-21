#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# Runtime builds enforce Guide provenance, dependency review, and frozen capability smokes.
exec uv run --group package --group memory-retrieval-benchmark python scripts/package/run.py "$@"
