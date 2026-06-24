#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "→ uv sync（workspace 模式，会编辑式安装 agent / llm-providers）"
uv sync

echo "→ 跑 spike 主入口"
echo
echo "  ⚠️  当前阶段：脚手架已建，代码未实现。"
echo "      执行 spike 验证清单会触发真实 DeepSeek API 调用(每个 case 跑一次 agent),"
echo "      按 llm-api-confirm 规则首次运行前需获用户明确授权。"
echo "      case 变体已 offline 固化到 cases/ 目录,不调用任何外部辅助模型。"
echo
exec uv run python -m agent_contract_eval_spike
