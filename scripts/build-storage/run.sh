#!/usr/bin/env bash
# Build admission reclaims only necessary safe caches; --automatic controls post-build maintenance.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/_lib/python312.sh
build_python
exec "$BUILD_PYTHON" -B scripts/build-storage/run.py "$@"
