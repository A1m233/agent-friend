#!/usr/bin/env bash
# scripts/agent-eval/run.sh - Run agent behavior contract evals.
# This may call real LLM providers and Tavily; get explicit user approval first.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run python -m agent_eval "$@"
