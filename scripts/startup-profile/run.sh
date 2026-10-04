#!/usr/bin/env bash
# installed-evidence checks isolated profile routing on Windows; read-only commands are cross-platform.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python scripts/startup-profile/run.py "$@"
