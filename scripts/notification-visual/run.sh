#!/usr/bin/env bash
# Settings target Notifications; explicit restart-bridge uses the existing supervisor guard.
set -euo pipefail
cd "$(dirname "$0")/../.."
. scripts/frontend/_ensure-node.sh
ensure_node_pnpm
exec node scripts/notification-visual/run.mjs "$@"
