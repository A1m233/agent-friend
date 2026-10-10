#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/_lib/python312.sh
build_python
exec "$BUILD_PYTHON" -B scripts/build-storage/run.py "$@"
