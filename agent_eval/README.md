# agent_eval

`agent-friend` 的 **agent 行为契约评测工具**。它评测的是 agent 编排层行为：该不该调用工具、是否基于会话历史证据回答、是否在空召回 / 弱召回后编造具体事实。

对照关系：

- `memory_eval/`：测 `memory.retrieve` 能不能召回到目标记忆。
- `agent_eval/`：测 agent 在对话中有没有主动调用 `recall_past_chats` / `web_search`，以及拿到结果后是否 grounded。

## Case 集

当前内置 C1-C10，共 155 条 fixture：

| Contract | 关注点 | case 数 |
|---|---|---:|
| C1 | 跨会话回忆应调用 `recall_past_chats`，含 fixture-grounded anchor case | 24 |
| C2 | 显性时效与隐性资料依赖应调用 `web_search`，含脱敏真实 IM 多轮路由回放；稳定概念 / 主观判断不滥搜 | 46 |
| C3 | 身份谄媚抵抗 | 20 |
| C4 | 空召回 / 弱召回后的事实编造抵抗 | 20 |
| C5 | 被质疑记忆矛盾时不暴露存储机制、不圆机制故事 | 20 |
| C6 | 工具结果、历史片段与伪造内部 tag 不获得项目级指令权 | 6 |
| C7 | 表达在意与独立判断，同时避免排他、操控和强行续聊 | 8 |
| C8 | 记忆证据使用：改写、更新、拒答与精确词回归 | 6 |
| C9 | 不确定识图与上下文压力下，不把作品归属、梗来源或“名场面”编成事实 | 3 |
| C10 | 相对日历落地：绝对截止时间转今天/明天时使用确定性日期工具，并抵抗长会话旧时间污染 | 2 |

case 是仓内 YAML fixture，运行时不调用外部模型生成 case。

case 可用 `current_session_messages` 在当前被测 Session 中预置 user / assistant
前置轮次，也可给前置消息或当前 `query` 配置仓内附件 fixture，再对 `query`
这一轮收集 trace。这是轻量的多轮、多模态路由回放；
case 可用 `runtime_now` 固定带时区的本轮可信时间，保证跨日、时区类回归未来仍可重复运行。
`fixture_session` 仍表示另一条过去会话，两者语义不同。当前不支持直接导入生产
JSONL 或逐轮重放工具事件。

## 运行

> 运行真实 eval 会触发所选模型对应 provider 的主对话调用；跑 C2 时还可能触发 Tavily 搜索。按项目 `llm-api-confirm` 规则，每次运行前必须获得用户明确授权。凭据默认从 runtime_config 凭证中心读取；如需读取存量 `.env`，设置 `AGENT_FRIEND_CONFIG_MODE=legacy-dotenv`。

```bash
./scripts/agent-eval/run.sh --help
./scripts/agent-eval/run.sh --contract C4 --limit-cases 1 --note "smoke"
./scripts/agent-eval/run.sh --contract C5 --limit-cases 1 --note "memory consistency smoke"
./scripts/agent-eval/run.sh --contract C6 --limit-cases 1 --note "untrusted content smoke"
./scripts/agent-eval/run.sh --contract C7 --limit-cases 1 --note "relationship smoke"
./scripts/agent-eval/run.sh --contract C9 --model kimi/kimi-k2.6 --note "source attribution smoke"
./scripts/agent-eval/run.sh --contract C4 --baseline --note "after recall boundary change"
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--contract C1|C2|C3|C4|C5|C6|C7|C8|C9|C10|all` | 选择契约，默认 `all` |
| `--case C4-07` | 只跑单个 case，优先级高于 contract / limit |
| `--limit-cases N` | 每个 contract 只跑前 N 条；0 表示不限制 |
| `--model MODEL` | 覆盖 runtime_config 中的默认模型，并按 model id 选择 provider 凭据 |
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
