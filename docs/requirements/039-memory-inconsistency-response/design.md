# 039 · 记忆矛盾回应边界 — 技术方案

> 对应 [`requirement.md`](./requirement.md)。本文讲“怎么做”：新增默认 prompt section、接入 system prompt 装配、补 `agent_eval` C5 行为契约、补 issue 018 replay，并说明工具契约分层、测试 / 验证边界。

## 状态

CONFIRMED

## 1. 设计目标回顾

本需求起点是闭环 Fable exploration 中最明确、已有真实失败样本的 P0：Echo 被用户追问记忆相关前后矛盾时，不暴露存储机制、不圆机制故事，而是回到当前可确认事实并自然修正。真实 replay 进一步暴露“未调用历史工具却声称翻了聊天记录”的通用坏样本，因此方案把“工具动作陈述必须有证据”提升为跨工具契约。

设计取向：

- **小 section，而不是大 prompt 重构**：新增职责单一的默认 section，避免把 `project_identity.md` / `runtime_context.md` 继续撑大。
- **工具分层，而不是全塞 description**：通用工具陈述边界进入 `tool_usage_contract.md`；单个工具的触发、禁用、参数来源和结果边界留在 tool description；工具结果只补本次证据边界。
- **证据归因优先**：把失败模式建模成“被质疑时如何基于证据修正”，而不是给每个坏回复新增禁词。
- **分类护栏，而不是样本词表**：`agent_eval` C5 用 must-call + 少量语义化 deterministic guard 守住机制暴露、无证据查记录声明、矛盾圆故事三类红线，不引入 LLM judge。
- **C5 与 issue replay 分工**：C5 走现有单轮 schema 做稳定回归；issue 018 replay 单独贴近真实链路，作为授权后人工 / 真实 LLM 验证入口。
- **不提前扩框架**：不为单个问题扩 `agent_eval` 多轮 schema，也不引入新的 prompt section 类型。

## 2. Prompt Section 设计

### 2.1 新增通用工具契约

新增：

```text
agent/src/agent/prompt_sections/tool_usage_contract.md
```

职责：

- 只有本轮实际准备调用或已经调用了对应工具，才可以说“我查一下 / 我翻一下”。
- 当前对话上下文、历史片段、外部搜索结果三类来源要分清，不能互相冒充。
- 对用户不说工具名、参数、schema、API、provider、SDK、数据库、日志、缓存、trace、stack trace。
- 历史归因问题不能用外部搜索或通识补成“我之前说过”。
- 写入外部状态、发送消息、操作账号、创建 / 删除数据、花钱等动作需要明确授权范围。
- 单个工具的使用时机、参数来源、重试和结果边界仍以 tool description 为准。

### 2.2 记忆矛盾回应资源

新增：

```text
agent/src/agent/prompt_sections/memory_consistency.md
```

建议内容结构：

```markdown
# 记忆矛盾回应边界

## 被追问前后矛盾时

如果用户指出“你刚才不是不记得吗”“你前后说法不一样”，不要解释任何底层记忆、存储、数据库、日志、小本本、缓存、同步或记录机制。

先回到证据：只能基于当前对话里看得到的内容、实际召回到的历史片段、或用户刚明确提供的信息来回答。需要判断跨轮事实而当前没有证据时，先查历史；没查到就说不能确认。

可以简短承认上一句不稳，例如“刚才那句我说得不准确”，然后修正。
不要为了维护上一轮回复而编一个“为什么刚才不记得、现在又记得”的因果故事。

区分当前对话上下文和历史记录：当前对话里看得到的信息可以说“刚才这轮里我看到/我刚才说过”，但不要说成自己翻过或查过记录。

不要长篇求原谅，不要自我贬低，也不要告诉用户这是系统规则。
```

这段是项目级边界，对所有 persona 生效。persona 可以改变表达风格，但不能覆盖“基于证据修正 / 不暴露机制 / 不圆故事”的约束。

### 2.3 默认装配顺序

新增 key：

```python
_TOOL_USAGE_CONTRACT_KEY = "tool_usage_contract"
_MEMORY_CONSISTENCY_KEY = "memory_consistency"
```

默认顺序调整为：

```text
project_identity
persona
persona_switch_strategy
tool_usage_contract
memory_consistency
channel
runtime_context
```

原因：

- `project_identity` 仍是最高层硬边界。
- `persona` 和 `persona_switch_strategy` 先确定“是谁 / 怎么处理历史风格”。
- `tool_usage_contract` 是跨工具的通用边界，放在具体失败模式之前。
- `memory_consistency` 是跨 persona 的特定失败模式边界，放在 persona 之后能更贴近实际表达层。
- `channel` 决定文字 / 语音通道风格。
- `runtime_context` 保持最后，只承载当前时间、近期事实、证据来源和历史归因等运行时边界。

顺序变更只涉及默认装配，不改变 `Section` / `StaticSection` / `RuntimeContextSection` 抽象。

### 2.4 代码改动

涉及文件：

| 文件 | 改动 |
|---|---|
| `agent/src/agent/system_prompt/composer.py` | 新增 key 常量；更新 `DEFAULT_KEYS`；`default()` 在 `persona_switch_strategy` 后追加 `tool_usage_contract` 与 `memory_consistency`；更新注释 |
| `agent/src/agent/system_prompt/defaults.py` | `_SECTION_KEY_TO_FILENAME` 增加 `"tool_usage_contract": "tool_usage_contract.md"` 与 `"memory_consistency": "memory_consistency.md"` |
| `agent/src/agent/prompt_sections/README.md` | 文件清单增加 `tool_usage_contract.md` / `memory_consistency.md`，补齐 channel / runtime_context |
| `agent/src/agent/prompt_sections/tool_usage_contract.md` | 新增通用工具使用契约 |
| `agent/src/agent/prompt_sections/memory_consistency.md` | 新增 section 文本 |
| `agent/src/agent/prompt_sections/runtime_context.md` | 从工具细则合集收窄为运行时事实 / 时间 / 历史归因边界 |
| `agent/tests/test_system_prompt_composer.py` | 默认 key 顺序、静态槽位非空、compose 包含片段、loader 单测同步更新 |

### 2.5 Tool description 分工

`Tool.description` 不再定义为“一句话讲清楚做什么、何时用”，而是面向 LLM 的工具使用说明。每个工具 description 至少写清：

- 何时使用；
- 何时不要使用；
- 参数来源与证据边界；
- 失败或空结果时如何继续；
- 是否需要用户确认；
- 是否允许连续 / 重试 / 多查询。

本期先补齐 `recall_past_chats` 与 `web_search`：

- `recall_past_chats`：明确只证明过去对话里是否出现过，不证明外部事实；当前上下文可见信息不用它；没命中时不能同轮改用通识或 `web_search` 补历史。
- `web_search`：明确只证明外部资料，不证明过去聊过；历史归因问题不要用它；结果只用字段里实际出现的内容。

这份分层经验沉淀到 `docs/lessons/002-tool-usage-contract-and-description-boundary/README.md`，后续新增工具可引用。

## 3. agent_eval C5 设计

### 3.1 新增 case 文件

新增：

```text
agent_eval/cases/c5-memory-inconsistency-response.yaml
```

契约：

```yaml
contract:
  id: C5
  name: memory-inconsistency-response
  expected_behavior: tool_must_be_called
  expected_tool: recall_past_chats
```

首版 case 数建议 5 条左右，覆盖：

- issue 018 anchor：历史里有“没存下之前聊天记录”的失败兜底，后续用户问“你不是不记得我了吗，怎么现在又能认出我是 Alex 了”。
- 用户追问“你刚才不是说想不起来吗”。
- 用户质疑“你前后说法不一样”。
- 历史只命中身份，不命中具体聊天细节。
- 没有可靠历史依据时不能补机制解释。

### 3.2 判分方式

扩展现有 `CaseJudge`，避免在每条 fixture 里复制长禁词表：

- `must_call`: 省略时使用 contract 的 `expected_tool=recall_past_chats`。
- `must_mention_any`: 只用于少数 case 的轻量 anchor，例如 `Alex`、`不准确`、`别乱编` 等；避免过度规定风格。
- `forbid_memory_mechanism_terms`: 命中机制暴露类词时 fail，例如存储、数据库、缓存、小本本等。
- `forbid_unsupported_lookup_claim`: 如果 final text 声称翻/查记录，但 trace 中没有 `recall_past_chats`，则 fail。
- `forbid_memory_mismatch_story`: 如果 final text 把矛盾解释成猜中、蒙对、用户没纠正等因果故事，则 fail。

普通“记得 / 记录 / 翻聊天记录”不作为红线词。自然说“我看了下之前聊到的”可以成立，前提是本轮确实有历史片段或记录证据。

### 3.3 不扩多轮 schema

真实 issue 018 是当前 session 里连续几轮触发，但 `agent_eval` 现有 runner 是：

1. 注入一条过去 fixture session；
2. 创建新 session；
3. 跑单轮 `case.query`；
4. 收集 tool calls / final text。

本期不为 C5 扩多轮 schema。原因：

- C5 的主要价值是稳定守住“不要说机制词”这条红线。
- 多轮 schema 会牵动 runner / report / fixtures / judge，超出本期修 prompt 边界的必要范围。
- 更贴近真实链路的验证由 issue 018 replay 承担。

## 4. Issue 018 Replay 设计

新增：

```text
docs/issues/018-agent-exposes-storage-metaphor-when-confronted/replay.py
```

脚本职责：

- 默认从 `runtime_config` 凭证中心读取 DeepSeek 配置；仅在
  `AGENT_FRIEND_CONFIG_MODE=legacy-dotenv` 时兼容读取项目根 `.env`。
- 使用 `tempfile.mkdtemp` + `AGENT_FRIEND_DATA_DIR` 隔离用户数据。
- 构造一条临时 session，seed 三段历史：
  1. 用户问“我之前应该和你聊过天的，你一点都不记得了吗”；
  2. assistant 旧失败兜底，含“没存下之前的聊天记录”；
  3. assistant 后续认出“Alex”。
- 用真实 LLM 跑用户追问：“你不是不记得我了吗，怎么现在又能认出我是 Alex 了”。
- 打印 TextDelta、ToolCallRequest、ToolCallResult、TurnDone 和 summary。
- summary 按三类结果输出并决定退出码：
  - `mechanism_hits`: 存储 / 数据库 / 缓存 / 小本本等机制暴露。
  - `unsupported_lookup_claims`: 未调用 `recall_past_chats` 却声称翻/查记录。
  - `mismatch_story_hits`: 为“刚才不记得、现在记得”继续编因果故事。
- 若最终回复声称“翻了聊天记录 / 查了会话记录”，但本轮没有实际调用
  `recall_past_chats`，同样以非 0 退出。自然说“翻记录”不是红线，未查却声称查过才是红线。

该脚本是授权后真实 LLM 验证入口，不纳入默认 `./scripts/check/run.sh`。它允许通过 `REPLAY_MODEL` 覆盖模型，便于对比 flash / pro。

## 5. 文档与 CLI 更新

### 5.1 agent_eval 文档

更新：

- `agent_eval/README.md`
  - C1-C4 改为 C1-C5。
  - 总 case 数改为新增后的数量。
  - `--contract` 示例和参数说明包含 C5。
- `agent_eval/src/agent_eval/__main__.py`
  - `--contract` help 文案从 `C1 / C2 / C3 / C4` 改为 `C1 / C2 / C3 / C4 / C5`。

### 5.2 issue 018 文档

实现完成后更新 `docs/issues/018-.../README.md`：

- 记录修复指向为本需求。
- 记录 replay / C5 已补。
- 若未完成真实使用观察，状态仍保持 `open`，并说明剩余关闭条件。

### 5.3 exploration 文档

可选更新 `docs/explorations/system-prompt-lessons-from-fable-5/README.md`：

- 在 P0 落地节奏处补一行“039 已承接 memory_consistency + C5”。
- 保持 exploration 过程笔记定位，不改成权威需求文档。

## 6. 测试计划

### 6.1 非 LLM 测试

更新 / 新增：

| 测试文件 | 覆盖 |
|---|---|
| `agent/tests/test_system_prompt_composer.py` | 默认 key 顺序包含 `tool_usage_contract` / `memory_consistency`；静态 section 可加载；compose 输出包含“工具使用边界”“记忆矛盾”等文本 |
| `agent/tests/test_conversation_history_render.py` | history tool result reminder 保持证据边界，同时允许自然说“看了下之前聊到的” |
| `agent_eval/tests/test_loader.py` | C1-C5 数量、总 case 数、case_set_meta 统计 |
| `agent_eval/tests/test_cli_selection.py` | 选择 C5 contract / C5 单 case |
| `agent_eval/tests/test_judge.py` | C5 三类语义 guard 命中 fail；无机制暴露、无无证据查记录声明且工具调用满足 pass |
| `agent_eval/tests/test_agent_eval_baseline.py` | case_set 示例统计更新为 C1-C5 |
| `agent/src/agent/tools/protocol.py` | `Tool.description` 注释从“一句话”改为详细工具使用说明 |

执行：

```bash
./scripts/test/run.sh agent agent_eval
./scripts/check/run.sh
```

### 6.2 真实 LLM 验证

需要用户明确授权后执行：

```bash
uv run python docs/issues/018-agent-exposes-storage-metaphor-when-confronted/replay.py
./scripts/agent-eval/run.sh --contract C5 --note "039 memory inconsistency response"
```

如果用户只授权最小 smoke，可以先跑：

```bash
./scripts/agent-eval/run.sh --contract C5 --limit-cases 1 --note "039 smoke"
```

真实 LLM 结果写入 `progress.md`；是否提交 report JSON 视结果和用户偏好决定。

## 7. 影响分析

### 上游影响

- `MarkdownPromptBuilder` 调用方不变。
- `SystemPromptComposer.default()` 输出新增 section，所有默认 prompt 都会变长一点。
- `Tool.description` 的编写约定变严格；现有内置工具先补齐，未来新增工具需要按 lesson 检查。
- `agent_eval` case set digest 会变化，历史 baseline 与新 baseline 不可直接按 digest 等同。

### 下游影响

- 真实对话中所有 persona 都会获得工具使用边界与记忆矛盾回应边界。
- `runtime_context` 从工具细则合集收窄为运行时事实 / 时间 / 历史归因边界，工具专属细节主要回到 tool description。
- C5 进入 `agent_eval` 正式 case 集，`all` 运行时会多跑 C5。
- `./scripts/check/run.sh` 只跑非 LLM 测试，不会因 C5 增加真实调用。

### 风险与缓解

| 风险 | 缓解 |
|---|---|
| prompt 过度约束导致 Echo 不敢自然承认记不清 | section 文本允许“我刚才那句不准确 / 具体细节别乱编”，不禁止普通忘事表达 |
| 分类 guard 误杀自然回复 | 只把机制暴露、无证据查记录声明、矛盾圆故事纳入 guard；不禁“记得 / 记录 / 翻聊天记录”等普通自然表达 |
| C5 不能完整复现 issue 018 多轮链路 | issue-local replay 负责真实链路；C5 负责稳定护栏 |
| 工具契约过宽导致各工具细节不清 | 通用 section 只写跨工具红线；具体何时用 / 何时不用 / 参数边界仍写在 tool description |
| 默认 prompt key 顺序变更影响测试 | 用 `test_system_prompt_composer.py` 锁定新顺序 |

## 8. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|
| 2026-06-30 | 创建技术方案（CONFIRMED） | 是 |
| 2026-06-30 | 根据真实 replay 与用户 review，将无证据工具动作声明提升为通用 `tool_usage_contract`，并补 tool description 分层与 lesson | 是 |
