#!/usr/bin/env bash
# Runtime doctor includes offline PNG/JPEG/WebP decoding and snapshot verification.
# It also verifies bounded sandbox recovery and unknown-start no-replay behavior.
# Session uploads are verified, copied, reopened, and cleaned offline by runtime doctor.
# Scheduled inputs verify fixed targets and durable request interruption without model calls.
set -euo pipefail
cd "$(dirname "$0")/../.."

# Installer smoke requires normal Windows shell paths; AppData overrides are rejected.
# Runtime builds enforce Guide provenance, companion hashes, and non-mutating helper load smoke.
exec uv run --group package --group memory-retrieval-benchmark python scripts/package/run.py "$@"
