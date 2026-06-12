# 013 · 记忆质量第一轮迭代（Pass 1）

> Memory Quality Pass 1 — 抽取保具体词 / pinned 不挤占 / 加宽召回
>
> 基于 [`012` 落下的首份 PerLTQA baseline](../012-memory-eval-anchor-recall-scoring/requirement.md) 与 [`issue 003` 根因分析](../../issues/003-memory-eval-baseline-2026-06-12/README.md)，对 [`008` 记忆系统](../008-engine-memory/requirement.md) 做第一轮**质量**改进——抽取阶段不再把具体词洗成话题摘要、pinned 通道不再挤占召回额度、检索通道不再"过稀"。本期是质量改进，不是结构补全。

---

## 1. 背景

### 1.1 当前现状

[`008`](../008-engine-memory/requirement.md) 把记忆系统的写入 / 召回 / 注入 / 可观测能力都落了；[`011`](../011-memory-recall-eval/requirement.md) 把评测机制搭起来；[`012`](../012-memory-eval-anchor-recall-scoring/requirement.md) 落了第一份判分器 + baseline。本期之前，008 各项 AC 都未失守（跨会话不失忆、能记住名字、可观测召回流水），但 **PerLTQA 全量首跑暴露了三个结构性弱点**（详见 [`issue 003`](../../issues/003-memory-eval-baseline-2026-06-12/README.md)）：

| 现象 | 占比 | 根因 |
|---|---|---|
| 2710 道题，**macro 平均 0.219**，**59.5% 零分** | — | 综合 |
| 零分题里 **53.5% 召回里只有 pinned** | 863/1613 | pinned 挤占了 episodic/semantic 的位置 |
| 零分题里 **41% 有 episodic 召回但仍 0 分** | 665/1613 | 抽取阶段把对话压成话题摘要，判分需要的具体词（专名、动作、物件、数量）被洗掉 |
| **45.5% 的题只拿到 ≤2 条召回** | 1232/2710 | FTS5 中文分词 + 抽取产物稀薄共同导致检索通道偏稀 |

→ 008 的 AC（工程能力）没失守，但 008 的**核心价值主张**——"长对话里 agent 真的记得用户讲过的具体词"——在 PerLTQA 上几乎做不到。

### 1.2 为什么现在做

- **baseline 已入仓**（commit `ef59c8e`），后续任何抽取 / 召回改动**都有量化对照锚点**；这是 011 + 012 一路铺到现在的最大杠杆，不立刻用就浪费了。
- **三块改动彼此独立、收益可叠加**：抽取改完单独能涨分、retrieve 改完单独能涨分、检索通道改完单独能涨分。每块都能独立观测、独立验证。
- **当前是 008 落地后第一个"评测驱动"的窗口期**——后续做得越多，再回头改抽取/召回越难（schema 兼容、迁移成本都会上升）。
- 与 engine 编排补全那条线**没有结构性冲突**（见 §1.3），可以并行做。

### 1.3 与已有工作的边界

| 概念 | 定位 | 与本期的关系 |
|---|---|---|
| [`008` 记忆系统](../008-engine-memory/requirement.md) | 抽取 / 存储 / 召回 / 注入 / 可观测的能力主体 | **不改 008 任何 AC**，不破坏对外接口（`Memory.observe` / `Memory.retrieve` 签名不变）；只改 008 模块内部实现质量 |
| [`011` 评测机制](../011-memory-recall-eval/requirement.md) | benchmark 加载 / 灌入 / 召回 / 展示 / 判分扩展点 | **不改 011 编排**；本期借 011 跑评测、用 012 判分器对照 baseline |
| [`012` 判分器 + baseline](../012-memory-eval-anchor-recall-scoring/requirement.md) | PerLTQA anchor recall scoring + 首份 baseline | **不改 012 判分器**；本期把它当尺子，量本期改进的提升量 |
| [`issue 003`](../../issues/003-memory-eval-baseline-2026-06-12/README.md) | 首份 baseline 的根因分析 | 本期承接 issue 003 列出的修复方向 1 / 2 / 3，**不含次因 4** |
| `docs/explorations/engine-completeness/` 那条线（agent main loop + hooks） | 编排层结构补全 | **可并行**——见下方"与编排补全线的协同契约" |

#### 与编排补全线的协同契约

本期改动**严格限制在 `memory/` 模块内部** + `memory_eval/` 评测产物，**不动 `agent/src/agent/conversation.py`**（包括 `_observe_turn` 调用时机、`memory.observe` / `memory.retrieve` 的调用位置）。

依赖假设（pass-1 假定 engine 补全线遵守）：

- `Memory.observe(fragment)` 与 `Memory.retrieve(query, ...)` 的**调用签名不变**
- `ConversationFragment` 喂给 observe 的**数据形状不变**（fragment 里 user / agent 原话不被压缩或类型化掉）

若 engine 线后续要改这两个接口或 fragment 形状，属于上游变更，pass-1 需相应跟进（不算 pass-1 的范围）。

**时序约定**：**pass-1 不等 engine 编排补全线**。两条线可独立合并，无前后置依赖。理由：

- pass-1 的评测路径（011 评测机制）是**直接调 `memory.observe` 灌入** PerLTQA 数据，不走 `agent.Conversation`，所以 engine 那边动 conversation.py 不影响 pass-1 baseline 的可重现性
- pass-1 合并前的兑现检查只有一条：确认本期窗口内 engine 线未提交"改 `Memory.observe / retrieve` 签名"或"改 `ConversationFragment` 形状"的变更（即上方两条依赖假设没被破坏）
- 若两条线先后合并，谁先谁后均可——engine 线把 `_observe_turn` 挪到 PostTurn hook 注册时，调用语义不变；pass-1 改的是 `Memory.observe` 内部抽取实现，调用方换不换 hook 都无感

### 1.4 本期不解决"全部记忆质量问题"

本期是**第一轮**质量迭代，明确不追求"把 macro 分数推到某个绝对水位"。本期交付的是三条**有量化方向、有可对照 baseline** 的改进路径，**改进量大小由评测结果体现，不预设具体数字 AC**（详见 §6）。

---

## 2. 本期范围（In Scope）

本期交付以下三块改进 + 一份对照 baseline，**仅限**这 4 项：

1. **抽取产物保留具体词**（对应 issue 003 主因 1）—— 改 `memory/extraction/` 内部，让抽取产物不再把对话压成话题摘要、保留判分需要的具体词（专名、时间、动作动词、数量词、物件名）。
2. **pinned 通道不挤占召回额度**（对应 issue 003 主因 2）—— 改 `memory/retrieval/` 内部 pinned 的注入策略，让"无论问什么都返回 pinned"的占位安慰品不再吃掉 episodic/semantic 的 top-K 位置。
3. **加宽检索召回**（对应 issue 003 主因 3）—— 改 `memory/store/` + `memory/retrieval/`，提升中文 query 命中率，把 recalled_count 中位数从当前的 2 拉起来。
4. **落对照 baseline**：本期改完后跑一次 PerLTQA 全量（31 samples × 全部问题），落到 `memory_eval/baselines/`，与 `2026-06-12T01-31-46-46e810d.json`（pre-pass-1 baseline）对照。

每一块都要：

- **能单独验证**：跑评测时可在 baseline 上单独打开一块，观察该块独立的影响（具体怎么做由 `design.md` 决定）；
- **不让另两块退化**：开启某一块后另两块的关键指标不应显著恶化（"显著"的口径由 `design.md` 给出量化标准）。

---

## 3. 非目标（Out of Scope）

以下能力本期**明确不做**：

- **judge 升级到 LLM-as-judge**（issue 003 次因 4）：当前阶段还轮不到——主因 1 没解决时升级 judge 只是把"召不到"变成"召不到但分数好看一点"。这一项留给未来评测层独立需求。
- **改动 `agent/src/agent/conversation.py`**：包括 `_observe_turn` 的调用位置 / 时机、`memory.observe` 调用方式、`memory.retrieve` 调用方式。这些由 engine 编排补全线负责，pass-1 不碰。
- **改 `Memory.observe` / `Memory.retrieve` 的对外签名**：包括参数表、返回类型。pass-1 是 008 接口的"内部实现质量改进"，对外契约不变。
- **改 008 schema 主结构**：`MemoryItem` 字段、pinned/episodic/semantic 三个 channel 的语义边界、`source` 字段等核心 schema 维持 008 现状；本期最多在不破坏兼容的前提下加内部字段（如抽取产物里多挂一份"原始片段引用"，详见 design.md）。
- **Reflection（异步反思）/ forget / inspect**：008 OOS 列表中的项，本期延续 OOS。
- **多 user / 多 persona 行为**：v1 锁单一 (user, persona)。
- **改前端 / bridge / 桌面端展示**：可观测产物对开发者可见即可，不做 UI 优化。
- **修改 011/012 已落的评测编排与判分器**：本期是 baseline 的"被测对象"侧，不动评测器本身。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；具体抽取策略、prompt 设计、pinned 注入算法、检索通道改造方案等"怎么做"由 `design.md` 决定。

### 4.1 抽取产物保留具体词（对应 issue 003 主因 1）

**目标**：让抽取阶段不再把"用户原话里的具体词"洗成"话题级摘要"。

- **R-4.1.1 抽取产物保留判分关键词类**：抽取产物文本中应保留对话里出现的：专有名词（人名、物件名、应用名）、时间副词、动作动词、数量词、特征词。**不允许**用"该应用"、"一些"、"各种"、"相关"等指代词替换原词。
  - **可观测口径**：以 issue 003 给出的反例（张小红 dialogue block `4_0_1#1`）为锚点。该段 PerLTQA 挂了 14 个 anchor 且**全部在用户原话里逐字出现**；pre-pass-1 baseline 的抽取产物保留的 anchor 字面词**几乎为 0**（仅"智能学习手环"覆盖到"手环触摸屏幕" anchor 的子串，其余 13 个 anchor 在抽取产物里无字面）。pass-1 改进后，该段抽取产物**应至少保留半数 anchor 字面词（≥7/14）**；其他类似"长 user 段被压成话题摘要"的反例由 `design.md` 选取一个小集合（推荐 3–5 个 dialogue block）作为同口径举证锚点。
- **R-4.1.2 抽取颗粒度匹配判分粒度**：抽取颗粒度应使得"单段连续 user 发言"里的关键事实/动作至少能在抽取产物里**找到对应承载条目**，不被合并为单一话题摘要丢失细节（具体颗粒度——逐 turn / 逐 user 块 / 其他——由 `design.md` 决定）。
- **R-4.1.3 不退化已经做对的部分**：008 已经做对的部分（pinned 抽取——名字 / 关系等身份事实；状态更新 R-4.1.5）pass-1 不退化。pinned 通道命中口径、`observe` 后是否落盘等"工程能力"指标维持 008 现状。
- **R-4.1.4 来源权重不变**：008 R-4.1.4（user-said 比 agent-said 权重高一档）维持不变。pass-1 只改"抽什么 / 怎么抽"，不改"哪边权重高"。

### 4.2 pinned 通道不挤占召回额度（对应 issue 003 主因 2）

**目标**：让 pinned（"用户名字是 X"这种永真事实）不再吃掉 episodic / semantic 的召回位置。

- **R-4.2.1 pinned 召回与 episodic / semantic 召回相互独立**：pinned 的注入路径（无论是相关性阈值过滤还是从 top-K 中剥离）应使得：**当 episodic / semantic 找不到匹配时，召回结果不再被 pinned 单独填充占位**。具体策略由 `design.md` 决定。
- **R-4.2.2 pinned 的存在不削弱 episodic / semantic 的曝光**：在 episodic / semantic 有匹配时，pinned 不应排挤它们的曝光位置。
- **R-4.2.3 显性引用场景不退化**：008 R-4.2.1（用户显式提起"你还记得 X 吗"）的命中率不下降——用户点名问"我叫什么"这种本来就该走 pinned 的场景，命中率维持 008 baseline。
- **R-4.2.4 调试可观测维持**：008 R-4.4 的 `last_memory_context`/召回流水可见性维持不变，并需能看到"本轮 pinned 是否注入 / 占用什么位置"。

### 4.3 加宽检索召回（对应 issue 003 主因 3）

**目标**：让中文 query 不再因为分词碎或抽取产物稀薄而拿不到匹配，把召回数从平均 ~2.6 拉起来。

- **R-4.3.1 中文 query 召回数明显抬升**：在 PerLTQA 评测下，**recalled_count 分布整体右移**（中位数 / 均值都明显上移；issue 003 已给出"recalled_count == 11 的样本均分 0.82"作为"召回再宽分就上来"的证据）。具体抬升幅度的口径由 `design.md` 给出。
- **R-4.3.2 不引入"召回再多但都不相关"的退化**：加宽召回后，**新增进来的召回条目的相关性不显著低于原有条目**——避免出现"召回数上去了、但 noise 变多、分数没上去"的局面。
- **R-4.3.3 检索通道改造对 008 行为透明**：008 R-4.2.1 / R-4.3.1（自动召回、显性召回）的对外行为不变；pass-1 是检索通道**内部实现改进**，对外只表现为"召回更全、相关性维持"。
- **R-4.3.4 新依赖（如向量化）需对项目可承受**：若引入向量化方案，模型 / 推理路径 / 启动开销需在项目可承受范围（具体由 `design.md` 评估，不在本文锁死）。

### 4.4 落对照 baseline

**目标**：让本期改进有"对照锚点 + 量化记录"。

- **R-4.4.1 落 pre-pass-1 / post-pass-1 两份 baseline**：post-pass-1 跑全量（31 samples × 全部问题），与 pre-pass-1（commit `ef59c8e` 落的那份）对照，落到 `memory_eval/baselines/` 下。
- **R-4.4.2 每块单独跑一遍**：除全开 baseline 外，本期应能跑出"仅开主因 1"、"仅开主因 2"、"仅开主因 3"三份切片 baseline，用于归因每块各自的贡献（具体跑法、是否需要 feature flag 由 `design.md` 决定）。
- **R-4.4.3 报告分析归档**：本期跑完后产出一份对照分析（与 issue 003 同等粒度），归档到 `docs/issues/` 或本需求目录下作为收尾产物（具体位置由实际产出时决定）。

---

## 5. 关键体验原则

- **质量优先于覆盖**：宁可少抽一些条目、但保留关键词；不要为了"覆盖更多话题"而把每条都做成话题摘要。
- **召回宁缺勿滥的边界**：pinned 不挤占召回额度的同时，**也不允许出现"召回完全为空"成为常态**（issue 003 baseline 里完全空召回只有 5 道）。
- **不破坏 008 的"工程能力"承诺**：跨会话不失忆、能记住名字、不重新自我介绍、调试可观测——这些工程层 AC 是 008 的 hard floor，pass-1 改进不应让它们退化。
- **改动对调用方透明**：所有改进都在 `memory/` 内部，调用方（`agent.Conversation`）感知不到内部变化；这是 §1.3 协同契约的前提。

---

## 6. 验收标准

本期不预设"macro 分数到某个绝对水位"的硬 AC（issue 003 自己也没说应该到多少；本期是第一轮迭代，目标是建立"可量化、可对照"的改进路径，而不是承诺一次到位）。

验收按以下四条收口：

| AC | 内容 | 验证方式 |
|---|---|---|
| **AC-1a 关键反例集合改善（统计层）** | issue 003 baseline 里"有 episodic 召回但仍 0 分"的 **665 道题**（issue 003 §现象第 3 行），pass-1 后**零分率必须有可观测下降**（具体下降幅度的量化口径由 `design.md` 给出，但**不允许零分率持平或上升**）。 | 跑 post-pass-1 全量 baseline，对照 pre-pass-1 该集合的逐题判分 |
| **AC-1b 关键反例样本改善（尖锐层）** | issue 003 列举的张小红 dialogue block `4_0_1#1` 的**连续 3 道零分题**，pass-1 改进后**至少 1 道不再 0 分**（任何 > 0 的得分都算改善）。 | 用 012 判分器跑该样本 |
| **AC-2 整体严格不退化（hard fail 线）** | 全量 macro 平均**必须严格不低于** pre-pass-1 baseline（**0.219**）。本期立项目的就是优化记忆质量，**任何形式的整体净退化都不接受**——即便部分样本提升、被其他样本退化对冲、总分持平也算未达预期（持平意味着"做了等于没做"）。"显著提升"是隐含目标，具体每块要求的提升幅度由 `design.md` 给出量化目标。 | 跑 post-pass-1 全量 baseline 对照 |
| **AC-3 三块各自可观测** | 仅开主因 1 / 主因 2 / 主因 3 时各自产出一份切片 baseline，每块**至少有一个量化指标**改善（主因 1 → 抽取产物 anchor 字面词保留率↑；主因 2 → "仅 pinned" 占零分比从 53.5% 下降；主因 3 → recalled_count 中位数从 2 抬升），具体指标定义与目标值在 `design.md` 中锁死。 | 跑 3 份切片 baseline |
| **AC-4 008 工程 AC 不失守** | 008 的工程能力 AC（跨会话记得名字、不重新自我介绍、调试可观测、persistence 不丢）在 pass-1 后继续通过。 | 重跑 008 既有验收用例 |

> 注：AC-1a/1b 形成"集合层 + 尖锐层"双口径——单样本（1b）防被绕过，集合（1a）防被单点过拟合。AC-2 是 hard fail 线，pass-1 必须**严格 ≥ baseline**。具体每块的量化口径（关键词保留率、"仅 pinned" 占零分比下降目标、recalled_count 中位数抬升目标）由 `design.md` 定义并在那里 commit。

---

## 7. 开放问题 / 待技术文档决策

以下问题由 `design.md` 回答，不在本 `requirement.md` 锁死：

- **Q-1 抽取颗粒度**：逐 turn 抽 / 逐 user 块抽 / 整段 dialogue block 抽哪种。是否引入"先粗后细"的分层抽取。
- **Q-2 抽取 prompt 改写形态**：在哪些位置加"保留具体词"的硬约束；是否需要 few-shot 示例；token 预算如何控制（issue 003 提到 prompt 强约束 + 颗粒度变细可能导致 LLM 调用次数显著上升）。
- **Q-3 pinned 注入策略**：是走相关性阈值过滤 / 从 top-K 中剥离 / 显式标注"附带"通道还是其他形态；阈值如何定。
- **Q-4 加宽召回的技术路径**：FTS5 中文分词改进（如 jieba / icu / nlp tokenizer）、向量召回（embed model 选型）、二者组合策略。
- **Q-5 切片 baseline 实现方式**：是用 feature flag / config 切换、还是分支化 commit、还是其他形态——影响 R-4.4.2。
- **Q-6 评测耗时 / 成本**：抽取颗粒度变细 + 向量化 + 全量重跑 baseline，预估总跑量与 token 成本。
- **Q-7 schema 兼容性**：本期是否在 `MemoryItem` schema 上引入新字段（如"原始片段引用"、"抽取颗粒度标记"），对已有存储的迁移如何处理。

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-12
- **确认时间**：2026-06-12
- **承接**：[`issue 003` 根因分析](../../issues/003-memory-eval-baseline-2026-06-12/README.md) 的修复方向 1 / 2 / 3
- **依赖 baseline**：`memory_eval/baselines/2026-06-12T01-31-46-46e810d.json`（commit `ef59c8e`）
- **下一步**：撰写同目录的 `design.md`（技术方案，含 Q-1～Q-7）
- **协同**：与 `docs/explorations/engine-completeness/` 那条线（agent main loop + hooks）可并行；协同契约见 §1.3
