#!/usr/bin/env bash
# scripts/agent-eval/run.sh - Run agent behavior contract evals.
# This may call real LLM providers and Tavily; get explicit user approval first.
# Credentials are resolved through runtime_config by default.
set -euo pipefail
export PYTHONUTF8=1
cd "$(dirname "$0")/../.."
exec uv run python -m agent_eval "$@"
