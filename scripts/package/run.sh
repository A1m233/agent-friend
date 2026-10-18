#!/usr/bin/env bash
# Runtime doctor includes offline PNG/JPEG/WebP decoding and snapshot verification.
# It also verifies bounded sandbox recovery and unknown-start no-replay behavior.
# Session uploads are verified, copied, reopened, and cleaned offline by runtime doctor.
# Scheduled inputs verify fixed targets and durable request interruption without model calls.
# QQ approval smoke uses local transport only; real client acceptance remains separate.
set -euo pipefail
cd "$(dirname "$0")/../.."

# Installer smoke requires normal Windows shell paths; AppData overrides are rejected.
# Runtime builds enforce Guide provenance, companion hashes, and non-mutating helper load smoke.
source scripts/_lib/python312.sh
build_python
exec "$BUILD_PYTHON" -B scripts/package/dispatch.py "$@"
