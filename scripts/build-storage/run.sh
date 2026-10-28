#!/usr/bin/env bash
# Build admission reclaims only necessary safe caches; --automatic controls post-build maintenance.
# inventory --root discovers history without adoption; reconcile --outcome records confirmed exit.
# adopt --cargo-evidence verifies receipt-less Cargo caches; gc --only-object-id narrows policy targets.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/_lib/python312.sh
build_python
exec "$BUILD_PYTHON" -B scripts/build-storage/run.py "$@"
