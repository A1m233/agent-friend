#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# Planning/frontend-only checks must not sync the Python workspace.
source scripts/_lib/python312.sh
build_python
exec "$BUILD_PYTHON" -B scripts/check/run.py "$@"
