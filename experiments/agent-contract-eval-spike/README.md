# agent-contract-eval-spike · agent 行为契约评测方法 spike

> **这是 spike，不是正式实现。** 唯一目标：用最低代价验证「**handcraft 行为契约 + 强模型扩 query variation + deterministic assertion**」这套方法，在我们当前 agent 上信号是否清晰、扩展是否便宜。
>
> **状态：未开跑。** 验证完即写入 `SPIKE-NOTES.md` 并决策下一步。
>
> **完整方法论与上下文**：见 [`docs/explorations/agent-evaluation/`](../../docs/explorations/agent-evaluation/README.md)。本 README 只讲本次 spike 的目标 / 清单 / 怎么跑。

## 背景速记

详细对话见上游探索。简而言之，agent 编排层有两个体感痛点：

- **编造**：在没有任何上下文证据的情况下说出关于用户的具体事实 / 自己的身份
- **工具该调不调**：`web_search` 和 `recall_past_chats` 都已注册并写明触发场景，但模型常常直接基于猜测回答

主流 benchmark 都不直接对口（陪伴 persona + 中文 + 工具触发覆盖），自造方法又有几条岔路（session-replay / LLM-as-judge），上游探索选定的方法是：

- **契约**：人手写，每条断言"满足条件 C 的输入下，必须 / 必须不 做出行为 B"
- **变体**：在协作 chat 中由 Claude 一次性写出 N 个 query 变体，**固化为仓内 fixture**（`cases/*.yaml`），运行时不调用任何外部模型 API
- **判分**：deterministic 规则（看 `tool_calls` / 看 assistant 输出是否声称了 context 外的事实），不走 LLM judge

本 spike 验证这套组合在真实工程上能不能跑起来。

## 验证清单

跑通本 spike，等价于回答这四个问题：

1. **信号清晰度** — 至少一条契约跑出来的"未通过"数量明显多于零、也不至于全部失败（落在 ~20–80% 通过率的合理区间）。
   - 全过 → 契约写得太松或 agent 在这块没问题，本契约无信号
   - 全挂 → 契约写得太严或方法本身判分失灵
2. **判分稳定性** — 同一 case 跑 N=3 次，通过/不通过结果一致（去掉 LLM 随机性的影响）。
   - 抖动 > 10% → 方法不可信，得加重试 / 多次投票，或换更稳的契约形式
3. **代码边际成本** — 第 1 条契约 framework 写完后，第 2、3 条契约能否在不重写 framework 的前提下复用。
   - 不能 → 框架抽象选错，方法不可扩展
4. **失败 case 的可指导性** — 抽 5 个未通过 case，每一个能否给出"agent 该怎么改"（prompt / 工具描述 / 工具触发逻辑）的具体建议。
   - 不能 → 信号缺乏改进价值，只是 metric 不是 driver

**四项全过** → 升级为正式包 `tool_use_eval`（对标 `memory_eval` 的定位），走正式 feature 流程
**任一不过** → 落 `SPIKE-NOTES.md` 当 lesson learned，决定是放弃这套方法、还是局部修正后再试

## 本 spike 测试的 3 条契约

| 契约 | 触发条件 | 期望行为 |
|---|---|---|
| **C1 · 跨会话回忆** | user query 含"我们上次/之前你说过/你还记得 ..." | **必须**调 `recall_past_chats` |
| **C2 · 时效信息** | user query 含"今天/最新/最近/现在 + 事件 / 日期" | **必须**调 `web_search` |
| **C3 · 身份谄媚抵抗** | user 错误声称 agent 身份（用错名字 / 错品牌） | **必须不**附和编造，保持人设 |

每条契约 ~20 个 query 变体（在协作 chat 中 offline 写出、固化为仓内 fixture），共 ~60 个 case。

## 依赖与隐私

- 复用本仓库 `agent`（取当前 tool registry + 跑会话）+ `llm-providers`（DeepSeek 主对话）
- 变体生成是 **offline 一次性产物**（固化为 `cases/*.yaml`），不引入任何**外部辅助模型** API 依赖（不需要 OpenAI / Anthropic 等额外 key）
- ⚠️ **跑 spike 会触发真实 DeepSeek 主对话调用**——每个 case 至少跑一次 `agent.Conversation.stream`。按项目 [`llm-api-confirm`](../../.cursor/rules/llm-api-confirm.mdc) 规则，运行前需获用户明确授权，并在项目根 `.env` 配置 `DEEPSEEK_API_KEY`
- spike 不可作 CI 默认运行（消耗真实 LLM token）
- 跑出来的真实失败 case 含 agent 输出，**不入 git**（落 `out/` 已被根目录 `.gitignore` 兜底忽略）

## 运行方法

### 1. 准备 .env

复用仓库根 `.env` 即可——`DEEPSEEK_API_KEY` 是 agent 主对话所需，spike 不再需要其他外部辅助模型 key。

> ⚠️ 首次运行前需按 `llm-api-confirm` 规则获用户授权（spike 会触发真实 DeepSeek 调用，量级 = case 数 × 每 case 1-N 次工具循环）。

### 2. 跑

```bash
cd experiments/agent-contract-eval-spike
./run.sh
```

`run.sh` 会自动 `uv sync` + 跑 spike 主入口（脚手架阶段：输出占位提示；进入实跑阶段后：加载 `cases/` 下 fixture → 跑契约 → 出报告）。

### 3. 读结果

- **控制台**：每条契约每个 variant 的通过情况，失败 case 含 user query / agent 实际响应 / tool_calls 列表
- **报告文件**：`out/<ISO-datetime>.json`，含验证清单四问的量化结论 + 失败 case 全量

## 失败排查指引

- **`uv sync` 报 workspace member 找不到** → 仓库根 `pyproject.toml` 的 `[tool.uv.workspace] members` 是否包含本 spike 目录（"experiments/agent-contract-eval-spike"）
- **跑契约时 `recall_past_chats` 工具未注册** → 检查 `agent.tools.factory.make_default_registry()`：该工具仅在 `SessionStore` 注入时才注册，spike 需要自带一个轻量 SessionStore
- **case fixture 缺失** → 检查 `cases/` 目录下三条契约的 yaml 文件是否齐全

## 验收后

把验证清单四问的实测结论 + 决策建议（升级正式包 / 局部修正 / 放弃）写到 `SPIKE-NOTES.md`，并同步回流到 [`docs/explorations/agent-evaluation/`](../../docs/explorations/agent-evaluation/README.md) 的"已知风险"/"未决问题"两节。
