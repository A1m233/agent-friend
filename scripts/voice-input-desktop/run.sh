#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec node scripts/voice-input-desktop/probe.mjs "$@"
