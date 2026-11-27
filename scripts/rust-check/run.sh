#!/usr/bin/env bash
# Windows test harnesses receive Common Controls v6 through the shared Python runner.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/_lib/python312.sh
build_python
exec "$BUILD_PYTHON" -B scripts/rust-check/run.py "$@"
