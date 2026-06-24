#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "→ uv sync（workspace 模式，会编辑式安装 agent / llm-providers）"
uv sync

echo "→ 跑 spike 主入口"
echo
echo "  ⚠️  当前阶段：脚手架已建，代码未实现。"
echo "      执行 spike 验证清单前会触发真实 LLM 调用（变体生成 + agent 真跑）。"
echo "      变体生成所用强模型首次启用前需走 llm-api-confirm 流程。"
echo
exec uv run python -m agent_contract_eval_spike
