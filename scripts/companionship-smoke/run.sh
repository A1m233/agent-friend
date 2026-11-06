#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1
# --section exposure observes four contact decisions without external messaging.
# --section social-identity uses the configured memory model and an isolated database.
# --section memory-commit checks identity synchronization and receipts; max 10 model calls.
cd "$(dirname "$0")/../.."
exec uv run python scripts/companionship-smoke/run.py "$@"
