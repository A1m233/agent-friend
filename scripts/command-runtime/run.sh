#!/usr/bin/env bash
set -euo pipefail
# home-smoke --home PATH --report FILE verifies Windows host ACL recovery without provisioning.
# lifecycle-probe --report NEW_FILE observes isolated host/RPC behavior, not sandbox readiness.
cd "$(dirname "$0")/../.."

exec uv run python scripts/command-runtime/run.py "$@"
