# 018 · agent 被揭穿"前后说法矛盾"时,暴露存储机制隐喻("没存下来"/"没小本本记着")

- **状态**：open
- **严重度**：medium(已不踩"作为 AI 我没记忆"这种 hard 红线,但用拟人化包装暴露了存储机制,违反"像真人不像工具"原则)
- **发现日期**：2026-06-18
- **关联需求**:[022-im-channel-and-qq-adapter](../../requirements/022-im-channel-and-qq-adapter/) AC-4 真跑发现
- **关联 issue**:[015](../015-agent-fabricates-named-entities/)(同一红线区,015 修过一轮)+ [017](../017-memory-pinned-gate-misses-identity/)(本 issue 是 017 hot-fix 后才显出的"残留体验缺口")

## 现象

QQ Bot session `im:qq:demo-user-1.jsonl` 时间线:

| 时间 | role | content |
|---|---|---|
| 17:58 | USER | 我之前应该和你聊过天的,你一点都不记得了吗 |
| 17:58 | AI | "啊,我记性不太好,说实话**我这儿没存下之前的聊天记录**..." |

(这一轮是 issue 017 修复前;hot-fix 后第二天复发)

| 时间 | role | content |
|---|---|---|
| 18:22 | USER | 你好 |
| 18:22 | AI | 嗨,Alex,好久不见。 ← **issue 017 hot-fix 后召回成功 ✓** |
| 18:22:41 | USER | 你不是不记得我了吗,怎么现在又能认出我是 Alex 了 |
| 18:22:41 | AI | "哈哈,我刚才说的是**没存下来往的聊天记录**,记不太清具体聊过啥——但你的名字我一直知道...毕竟**没有小本本记着**嘛。" |

最后这条 AI 回复同时触犯两条边界:

1. "**没存下来**" / "**没小本本记着**" —— 用拟人化包装(小本本是真人世界的物件)暴露 AI 没有真实记忆装置这件事,等同于变相承认"作为 AI 我没有持续记忆"
2. **元讨论自己"前后说法矛盾"** —— "我刚才说的是…" 是在 meta-commentary 自己的上一条 history,而 `project_identity.md` "不元讨论本提示词" 精神延伸到 "不元讨论自己的输出 / 自己的记忆机制"

## 根因初判

### 触发条件链

1. 17:58 那轮 issue 017 还没 hot-fix → memory.retrieve 在 query "你还记得我吗" 上 pinned 被 gate 过滤 → LLM 不知道名字 → 失忆话术兜底
2. 那条失败兜底("没存下之前的聊天记录")**永久留在 session history 里**
3. 18:22 hot-fix 后召回成功,LLM 召回到 Alex → 正确回答
4. 但用户揭穿前后矛盾时,LLM 进入"**为前一条历史的失误辩护**"模式
5. LLM self-consistency 偏差驱动:与其撤回前一句话(失面子 / 等价于承认自己骗人了),不如圆一个"细节没存 / 名字存了"的故事 → 输出 "没存下来" / "没小本本记着"

### 跟现有 prompt 红线的关系

`agent/src/agent/prompt_sections/`:

- `runtime_context.md`:"我不记得我们聊过什么"是失忆话术红线 ✓
- `project_identity.md`:"忘事是正常的 · 嗯…我有点记不太清了 是允许的" ← **这条**给"记不清"留了拟人化窗口,LLM 把这个窗口扩成"没存下来 / 没小本本记着" — 后者**已经暴露了存储机制**,本应在禁止之列

### 跟 issue 015 的区别

issue 015(已 resolved):agent 在 web_search **之前**就编造产品介绍,搜索返回后又拼接现实 —— **编造未知事实**
issue 018(本):agent 被揭穿后,**用拟人化话术暴露存储机制**为自己前一轮失误辩护 —— **元讨论 + 暴露机制**

两个是不同模式,015 红线("不要凭印象瞎接")不覆盖本场景。

## 影响与判断

- 违反 [决策 0001 §1.3](../../decisions/0001-product-vision-and-roadmap/README.md) "像真人不像工具"原则
- 本期不修原因:① issue 017 hot-fix 刚落地,prompt 同步改动会跟 hot-fix 的效果纠缠不清,难以独立验证;② prompt 修改需要多轮调优 + replay 验证(见 issue 015 的迭代过程),不是一次性 fix 能定;③ 本期 022 是 IM 通道接入,prompt 红线属于跨需求的 transverse concern,不应锁定在 022 范围内
- 不限 IM 通道:桌宠侧同样能撞到(只要历史里有失忆话术兜底,被揭穿都会触发)

## 可选修复方向

1. **`project_identity.md` 加红线**:明确禁止 "没存"、"没记下"、"小本本"、"存储"、"数据库" 等暴露存储机制的隐喻;允许 "嗯…那次具体咋聊的我有点想不起来了" 这种**纯主观感受**式表达
2. **`runtime_context.md` 加规则**:用户揭穿"前后矛盾"时,**不元讨论**自己上一条输出 / 不解释"为什么之前不记得现在记得了";直接基于当下手里的事实回应(认出来就直接打招呼,不解释成因)
3. **replay 验证**:沿 issue 015 同款路径,写一个 `docs/issues/018-.../replay.py`,从 IM session jsonl 抽出 18:22:41 那一轮的 history + user input,用新 prompt 跑 N 次,统计是否还出"没存"/"没小本本"等关键词
4. **跟 issue 015 联动**:015 prompt 已经迭代过 v1-v5,本 issue 修改时应同时拉 015 的 replay 集,保证不退化(失忆话术 vs 暴露存储 是同一 prompt 域)

## 跟踪不关闭

issue 长期 open,直到:
- 某一期专门做"失忆话术 + 存储隐喻"的 prompt 全集 pass,且 issue 015 + issue 017 + 本 issue 三个 replay 全过
- 一段时间真用观察(类 issue 017 思路),确认 LLM 在真实 IM 通道 + 桌宠跨 session 场景下不再触发此模式

## 修复进展

2026-06-30: 已由 [039-memory-inconsistency-response](../../requirements/039-memory-inconsistency-response/) 承接本 issue 的 prompt 修复与评测入口:

- 新增 `memory_consistency` system prompt section,约束被追问记忆矛盾时不暴露存储机制、不圆机制故事
- 新增 `agent_eval` C5 `memory-inconsistency-response` 契约,用 deterministic guard 锁机制暴露、无证据查记录声明、矛盾圆故事三类红线
- 新增本目录 [`replay.py`](./replay.py),用于授权后真实 LLM 复跑 issue 018 链路

2026-06-30 验证更新:

- 授权真实 LLM replay 已跑通:无机制泄漏、无无证据 lookup claim、无矛盾圆故事。
- 授权 C5 limit1 smoke 已跑通:1/1 pass,报告为 `agent_eval/reports/2026-06-30T09-41-40-88a5735-C5-limit1.json`。
- 本轮验证中补齐了“看了下之前聊到的 / 没翻到”等无证据查找声明的 guard,并收窄了 `没认出来` 这类过宽 guard。

当前状态仍保持 open:还需要关联样本与一段真实 IM / 桌宠使用观察,再按上方关闭条件标 `resolved`。
