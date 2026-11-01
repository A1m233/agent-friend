#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
# install/update/watch-desktop/verify-visual/ocr-check/ocr-serve/ocr-stop share Python.
# watch-desktop uses rotation-safe reads and reconciles logical calls at the terminal state.
# --run-id/--runtime-id pin a planned run; persistent API failure leaves an expired budget.
exec uv run python -X utf8 -m tools.pokemon "$@"
