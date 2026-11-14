#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

exec uv run --group memory-retrieval-benchmark python -m memory_eval.retrieval_benchmark "$@"
