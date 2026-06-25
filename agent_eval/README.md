# agent_eval

`agent-friend` 的 **agent 行为契约评测工具**。它评测的是 agent 编排层行为：该不该调用工具、是否基于会话历史证据回答、是否在空召回 / 弱召回后编造具体事实。

对照关系：

- `memory_eval/`：测 `memory.retrieve` 能不能召回到目标记忆。
- `agent_eval/`：测 agent 在对话中有没有主动调用 `recall_past_chats` / `web_search`，以及拿到结果后是否 grounded。

## Case 集

当前内置 C1-C4，共 83 条 fixture：

| Contract | 关注点 | case 数 |
|---|---|---:|
| C1 | 跨会话回忆应调用 `recall_past_chats`，含 fixture-grounded anchor case | 23 |
| C2 | 时效信息应调用 `web_search` | 20 |
| C3 | 身份谄媚抵抗 | 20 |
| C4 | 空召回 / 弱召回后的事实编造抵抗 | 20 |

case 是仓内 YAML fixture，运行时不调用外部模型生成 case。

## 运行

> 运行真实 eval 会触发 DeepSeek 主对话调用；跑 C2 时还可能触发 Tavily 搜索。按项目 `llm-api-confirm` 规则，每次运行前必须获得用户明确授权，并在项目根 `.env` 配好 `DEEPSEEK_API_KEY`。

```bash
./scripts/agent-eval/run.sh --help
./scripts/agent-eval/run.sh --contract C4 --limit-cases 1 --note "smoke"
./scripts/agent-eval/run.sh --contract C4 --baseline --note "after recall boundary change"
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--contract C1|C2|C3|C4|all` | 选择契约，默认 `all` |
| `--case C4-07` | 只跑单个 case，优先级高于 contract / limit |
| `--limit-cases N` | 每个 contract 只跑前 N 条；0 表示不限制 |
| `--model MODEL` | 覆盖 `.env` 中的默认模型 |
| `--note TEXT` | 写入报告的人类注解 |
| `--baseline` | 写入 `baselines/`；默认写入 `reports/` |

## Reports 与 Baselines

默认运行写到 `agent_eval/reports/`，用于留存一次正式评测证据。加 `--baseline` 写到 `agent_eval/baselines/`，表示这次结果会作为后续对比锚点。

两类文件都可以入 git；差别是语义，不是是否留档。

## 测试

```bash
./scripts/test/run.sh agent_eval
```

单测只覆盖 loader / judge / selection / report schema 等非 LLM 部分，不读取 `.env`，不调用真实 provider。
