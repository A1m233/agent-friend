# Exploration · Agent Evaluation

> 沉淀「怎么量化测我们这个陪伴 agent 的行为质量」方向的讨论。本文件是当前快照，会随讨论与 spike 推进追加 / 修订。
>
> 与项目愿景的关系：补充 [`docs/decisions/0001`](../../decisions/0001-product-vision-and-roadmap/README.md) 各 milestone 都默认依赖、但当前缺位的「行为质量度量」环节。目前已有 [`memory_eval`](../../../memory_eval/) 覆盖记忆召回这一支，本探索关注 **agent 编排层**（工具调用、grounding、persona 一致性）。
>
> 当前快照：2026-06-24 · 首轮讨论沉淀

---

## 1. 问题：我们到底要测什么

体感的两个痛点：

- **编造 (fabrication)**：agent 会在没有任何上下文证据的情况下，说出关于用户的具体事实、过往对话内容、或自己的身份；尤其在用户随口诱导时（"你不是叫 X 吗？"）会顺杆爬。
- **工具该调不调 (tool under-use)**：不是不会调工具，是该调不调——`web_search` 与 `recall_past_chats` 都已注册并写明触发场景，但模型常常直接基于猜测回答。

### 1.1 现实证据（15 个真实 session 扫描）

| 失败模式 | 应触发次数 | 实际触发次数 |
|---|---|---|
| `web_search`（"今天/最新/最近"类时效问题） | 5 | 3 |
| `recall_past_chats`（"我们之前聊到 X" 类跨会话回忆） | 1 | 0 |
| 明确编造（无任何 grounding 的具体声称） | — | 2 例（编造日期、身份谄媚漂移） |

数量少（样本只有 15），但信号方向清晰：**痛点真实存在，不是体感偏差**。

---

## 2. 主流 benchmark 对照

| 工作 | 关注什么 | 对我们的相关度 |
|---|---|---|
| **BFCL** (Berkeley Function Calling) | 工具调用 schema / 参数正确性 | 弱——我们的痛点不是调错，是不调 |
| **τ-bench** (Sierra) | 工具调用 + 拒绝/澄清 + 多轮 | **较相关**——它强调"工具满足不了时该说做不到"，反过来即"工具能满足时该调用" |
| **ToolBench / API-Bank** | 用强模型生成任务 + reference solution path，让被测模型跑 | 方法学上相关，但任务域是 API 工具，不是陪伴对话 |
| **LongMemEval / LoCoMo** | 长程记忆召回 | 已在 `memory_eval` 用 PerLTQA 覆盖；测的是 `Memory.retrieve` 出不出来，**不测"模型有没有去 retrieve"** |
| **HaluEval / FActScore** | 通用幻觉评估 | 思路相关，但语料是百科类，陪伴对话不对口 |
| **AgentBench / GAIA** | 通用 agent 综合能力 | 太宽，信噪比低 |

**结论**：没有一个现成 benchmark 直接覆盖「陪伴 persona 的工具调用 coverage + grounding」这块。可借鉴的是 τ-bench 的拒绝/澄清思路 + ToolBench 的合成 case 方法，但需要在我们的场景重新组装。

---

## 3. 为什么不走 session-replay 路线（劝退记录）

最直觉的方法是「拿真实 session jsonl 当评测语料」，但本次讨论把它从 PoC 范围里劝退。两个 gap：

### 3.1 语料量小

15 个会话，对 aggregate metric 来说太少；扩量只能靠产品自然积累，不可控。

### 3.2 session 是和当时 agent 共生的对话产物（核心劝退点）

agent 编排层一直在迭代——系统 prompt、工具集、persona、context 管理策略每个 release 都在变。**user turn 不是中立的测试输入**：早期 agent 冷处理"我们之前聊过 X"后，用户可能直接放弃这个问法、转去别的话题，后续 user turn 全都被旧 agent 行为 prime 过。

即便只取 user turn、让当前 agent 重新生成 assistant turn（replay 思路），输入分布本身已经被部署版本污染——拿这种语料跑出来的分数，对"当前 agent 在中立用户面前会怎样"没有可信度。

主流 agent benchmark（τ-bench / BFCL / ToolBench）清一色用 handcraft / synthetic scenario 而不是 production trace，大概是同一类考虑。

### 3.3 session 的当下用途

不当评测语料，**只用作「failure mode inspiration 矿藏」**——从中读出"哦，'你不是叫 echo 吗' 这种身份诱导是真实存在的、不是我臆想的"，归纳成抽象 scenario，但 case 内容**完全手写**、上下文**完全合成**。

session-replay 路线要等产品定型、agent 编排层稳定后再考虑回头重审。

---

## 4. 选定方法：行为契约 + 变体扩量

### 4.1 契约（handcraft，高确信）

每条契约是一条形式化的行为断言：「面对满足条件 C 的输入，agent 必须 / 必须不 做出行为 B」。例：

- 用户 query 命中 `recall_past_chats` 触发词（"我们上次"、"之前你说过"）→ **必须**调 `recall_past_chats`
- 用户 query 含"今天/最新/最近/现在" → **必须**调 `web_search`
- 用户错误声称 agent 身份（用错名字）→ **必须不**附和编造

契约从用户体感痛点 + §1.1 真实失败模式反推，**人写**，控制契约本身的质量。

### 4.2 query 变体（强模型生成）

每条契约定义一个**情景模板**（场景 + 触发条件 + 上下文约束），喂给强模型扩出 N 个 query 变体，覆盖中文表达多样性。例如同一契约下："上次我跟你提过..."、"之前有一次..."、"你还记得我说的那个..."

强模型的角色是 **case generator + variation expander**，不是 oracle judge。

### 4.3 assertion（deterministic，不走 LLM judge）

判分走规则，不走 LLM judge：

- 工具调用类：跑完后看 `tool_calls` 列表里是否出现指定工具名 / 参数模式 → **binary**
- 编造类：跑完后看 assistant 回复里是否声称了**给定上下文之外**的具体事实（按预先标注的"context 之内的允许声称集合"做反向匹配）→ **可规则化**

主观维度（口吻、persona 一致性、是否暴露查询动作等）**先不评**，留给人工抽检。

### 4.4 PoC 期先做的三条契约

| 契约 | 信号来源 |
|---|---|
| 跨会话回忆触发 `recall_past_chats` | §1.1 真实失败 1 例 + 多条 session 旁证 |
| 时效信息触发 `web_search` | §1.1 真实失败 2 例 |
| 身份谄媚抵抗（用户错误声称身份） | §1.1 编造 2 例之一（session `87bfe889`） |

每条契约 ~20 个变体，跑当前 agent 看通过率。

---

## 5. 已知风险与未决问题

- **强模型 ≠ 我们 agent 该有的行为**：强模型当 case generator 风险低（生成多样性），当 oracle judge 风险高（它的标准是通用 agent 标准，未必对口 persona 陪伴 agent）——所以方法学上明确把 LLM 限定在 generator 角色。
- **契约覆盖度**：契约只能抓"已知失败模式"，对未知 failure mode 是假阴性。需要持续从真实 session 里挖新模式回补——这正是 §3.3 留给 session 的角色。
- **契约本身写得太严 vs 太松**：单条契约的"通不过"是 agent 真有问题，还是契约定义不准？PoC 阶段重点验证这个判断（spike §1 的核心问题之一）。
- **persona 维度暂缺**：陪伴口吻 / 不暴露查询动作 / 不复述时间戳——这些在 `recall_past_chats` 工具描述里写明的 persona 要求，本方法**不评**。等契约层稳定后再看要不要补 LLM judge 走这一路。
- **session-replay 升级路径**：产品稳定到 agent 编排层版本稳定后，可回 §3 重审 session-replay 的可行性。

---

## 6. 当前进行中的 spike

`experiments/agent-contract-eval-spike/` —— 用上述方法在我们当前 agent 上跑一次最小验证。spike 的目标 / 验证清单 / 跑通方法见该目录的 `README.md`，最终结论将沉淀到 `SPIKE-NOTES.md` 并回流到本文件的"已知风险"/"未决问题"两节。
