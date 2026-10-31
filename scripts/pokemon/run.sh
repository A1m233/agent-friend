#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
# install/update/watch-desktop/verify-visual/ocr-check share the Python implementation.
# watch-desktop uses rotation-safe reads and reconciles logical calls at the terminal state.
exec uv run python -X utf8 -m tools.pokemon "$@"
