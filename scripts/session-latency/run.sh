#!/usr/bin/env bash
set -euo pipefail
# responsiveness measures fixed-tail synthetic history growth offline; --output must be new.
# desktop-responsiveness measures real WebView timing and delayed-history input isolation; never sends.
# desktop-growth-fixture extends only synthetic history prefixes, keeping the tail unchanged.
# legacy-read uses an isolated explicit journal copy; desktop-send-smoke requires LLM authorization.
# prepare/scroll/lock-compare observe; real/isolated serve/long-runtime require separate LLM authorization.
# offline sends on disposable copies, blocks network, and reports derived storage costs.
# cold-cache compares fresh/retained derived caches on disposable copies; no provider calls.
# desktop-fixture --synthetic-only prepares 2/65-turn histories, full bodies and oldest-message references; never launches.
# desktop-history checks original UI, complete turn pages, grouped tools and reference paging; never sends.
cd "$(dirname "$0")/../.."
exec uv run python scripts/session-latency/run.py "$@"
