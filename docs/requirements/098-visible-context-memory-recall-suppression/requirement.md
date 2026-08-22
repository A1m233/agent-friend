# 098 · 当前可见对话的自动召回抑制

> Visible Context Memory Recall Suppression

## 状态

CONFIRMED

## 1. 背景

[048](../048-memory-retrieval-quality-pass-2/requirement.md) 已统一自动记忆召回与显式历史查询的检索编排，
[063](../063-context-compaction-reliability/requirement.md) 已让长会话通过摘要维持在模型预算内，
[069](../069-session-aware-conversation-history-retrieval/requirement.md) 又统一了有效会话分支与历史查询边界。

现有自动召回仍可能把当前会话中尚在模型原始上下文里的内容再次作为记忆注入。若简单排除当前会话或最近 N 轮，
又会让已经被压缩、移出原始上下文的旧内容永久失去召回机会。

本需求只补 memory 的召回范围契约：调用方请求自动记忆时，同时提供当前会话以及“允许召回到哪一轮”的截止边界。
memory 不理解压缩、摘要、FIFO 或模型窗口，只按调用方给出的边界过滤当前会话候选。

例如当前会话有 A B C D E F G 七轮，调用方知道 A-D 已被压缩、E-G 仍由原始上下文承担记忆作用，
于是请求“召回当前会话 D 及以前的记忆”。当前会话中来源位于 D 及以前的普通 Semantic、Episodic 与原始
对话候选可以参与自动召回，E-G 对应候选被抑制。下一次压缩覆盖到 G 后，调用方在后续自动召回中把截止
边界推进到 G。

## 2. 目标

- 当前会话仍由原始上下文承载的非 Core 证据不被自动召回重复注入。
- 当前会话已经位于调用方截止边界及以前的旧证据重新获得自动召回资格。
- 普通 Semantic、Episodic 与 automatic SessionHistorySource 原始对话候选共享同一截止语义。
- Core 核心身份作为唯一例外，保持始终召回，不受截止边界影响。
- 其他会话的相关候选不受当前会话边界误伤。
- 显式历史查询继续按用户约束工作，不应用 automatic recall 的当前会话边界。
- memory API 只表达召回范围，不反向依赖 context 模块或压缩实现。
- trace 能说明候选因截止边界、Core 豁免或会话归属得到的决定。

## 3. 核心语义

### 3.1 当前会话自动召回范围

一次生产自动召回必须携带当前会话范围：

- current_session_id：本轮正在继续的会话。
- recall_through_turn：当前会话中允许自动召回到的已完成 turn；可以为空。

recall_through_turn 由调用方确定。memory 只把它当作当前会话内的顺序边界，不读取 compaction checkpoint，
也不判断为什么这一轮及以前已经可以召回。

### 3.2 非 Core 候选

- 当前会话候选的来源 turn 位于截止边界及以前时，可以参与自动召回。
- 当前会话候选的来源 turn 位于截止边界之后时，不参与本次自动召回。
- 截止边界为空时，当前会话全部非 Core 候选不参与本次自动召回。
- 其他会话候选继续按既有相关性、有效性、融合和预算规则召回。
- 恢复资格不等于无条件注入；候选仍须通过既有检索流程。

本语义统一适用于：

- 普通 Semantic；
- Episodic；
- automatic SessionHistorySource 返回的原始对话候选。

### 3.3 Core

Core 核心身份信息始终召回。无论当前会话是否有截止边界、边界位于哪一轮、Core 的来源是否仍在当前原始历史中，
都不应用本需求的抑制规则。项目不再使用 pinned 作为本需求中的独立层或例外概念。

## 4. 范围

### 4.1 包含

#### memory API 与统一过滤

- 自动召回接口能够接收当前会话和可选截止 turn。
- 统一检索编排在排序、去重与配额之前应用当前会话边界，使被抑制的高分候选不会阻止更旧候选补位。
- 三类非 Core 候选均能通过现有来源信息归属到来源会话与来源 turn。
- 已知属于当前会话、但无法可靠判断来源 turn 的候选不得绕过边界；兼容策略由技术方案确定。
- 其他会话候选不应用当前会话边界。

#### 调用方薄接线

- 持久化 Conversation 发起自动召回时传入当前 session id。
- Conversation 复用已有最近有效 compaction 的覆盖终点作为截止边界；无有效 compaction 时传入空边界。
- 本轮新产生并成功提交的 compaction 从后续自动召回开始生效，不要求为了同一模型请求重排 recall 与 context。
- 非持久化、probe、显式工具和测试入口按各自语义选择是否提供自动召回范围，不被强制伪造成某个会话。

#### 既有边界与可观测性

- inactive 分支、当前未完成轮、已硬删除内容、owner 和 persona 等既有有效性边界继续优先生效。
- trace 能记录当前会话、截止 turn，以及候选因位于边界前后、属于其他会话、属于 Core 或来源顺序未知而产生的决定。
- 机制性行为使用确定性 fixture 验证，默认测试不调用真实外部 LLM。

### 4.2 不包含

- 不修改 ContextManager、BuildResult、上下文组装顺序、token 预算、摘要或 FIFO 策略。
- 不要求 context 产出新的完整 visibility snapshot、共享 frontier API 或持久化投影事件。
- 不解决本次请求中新 compaction 与此前 recall 之间的即时重排；新边界从后续召回生效。
- 不改变 Core 的抽取、存储或始终召回行为。
- 不改变普通 Semantic、Episodic 的抽取、纠错、retirement、consolidation 或排序质量。
- 不改变显式历史查询工具的查询能力与结果范围。
- 不建设通用语义重复判定器；本期按来源会话与来源 turn 建立确定性边界。
- 不新增用户设置、记忆管理 UI、手动压缩入口或可见性调试界面。
- 不实现并行需求 097 的 Extractor 邻近上下文、speaker attribution、纠错或 retirement 行为，也不为其交付 context snapshot。

## 5. 与 097 的责任边界

098 只交付 memory 自动召回的范围参数与过滤行为，不产出新的 context 可见性底座。

097 若需要在 Extractor 中使用目标 turn 附近的可见原始历史，应由 097 自己复用调用方已有边界，或另行与 context
确定其输入契约；不得把该能力作为 098 memory API 的前置交付。用户已决定先完成 098，再继续 097，但两者不再
存在“098 必须先产出完整 visible-source snapshot”的技术依赖。

## 6. 核心需求

### R-1 当前会话非 Core 候选服从截止边界

- 普通 Semantic、Episodic 与 automatic SessionHistorySource 使用同一当前会话截止判断。
- 无截止边界时，当前会话全部非 Core 候选被抑制。
- 截止边界之后产生的当前会话候选被抑制。

### R-2 截止边界及以前的旧候选恢复资格

- 来源 turn 位于截止边界及以前的当前会话非 Core 候选可以参与自动召回。
- 边界推进后，新纳入旧区间的候选随之恢复资格。
- memory 不需要知道边界推进的原因。

### R-3 Core、其他会话与显式入口不受影响

- Core 始终召回，且是唯一永久豁免层。
- 其他会话候选按既有规则召回。
- 显式历史查询和 probe 不应用生产 automatic recall 的当前会话边界。

### R-4 调用边界稳定且可解释

- memory 只消费当前会话与截止 turn，不依赖 context 类型或 compaction schema。
- 来源顺序解析复用会话事件的 active lineage，不另建历史事实来源。
- trace 能解释边界过滤、Core 豁免、其他会话放行及来源顺序未知等决定。

## 7. 验收标准

- **AC-1 无压缩边界**：生产自动召回携带当前 session、截止 turn 为空时，该会话产生的普通 Semantic、Episodic
  和 SessionHistorySource 候选均不参与自动召回；Core 仍召回。
- **AC-2 首次截止边界**：调用方传入当前 session 与截止 turn D 时，来源位于 D 及以前的三类非 Core
  候选可以参与召回，来源位于 E-G 的候选被抑制。
- **AC-3 边界推进**：后续调用把截止 turn 从 D 推进到 G 后，当前会话中 E-G 对应候选恢复召回资格。
- **AC-4 新 compaction 后续生效**：某轮 context 新提交覆盖到 G 的 compaction 时，本轮无需重新运行 recall；
  下一次自动召回使用 G 作为新截止边界。
- **AC-5 Core 永久召回**：在空边界、边界为 D、边界推进到 G 及 Core 来源仍很新的情况下，Core 均保持召回。
- **AC-6 其他会话不受影响**：当前会话边界为任意值时，其他会话的相关非 Core 候选仍按既有规则参与召回。
- **AC-7 跨数据源一致**：同一来源 turn 对应普通 Semantic、Episodic 和原始对话候选时，三者得到一致的边界判断。
- **AC-8 来源顺序未知时不绕过**：已知属于当前会话但无法定位来源 turn 的候选不会被误当作边界前自动放行，
  且有明确 trace。
- **AC-9 生命周期边界**：inactive、当前未完成、硬删除及越过 owner/persona 边界的候选，不会仅因位于截止
  turn 以前而恢复资格。
- **AC-10 显式历史查询例外**：用户主动查询当前会话历史时，显式工具继续返回满足约束的证据，不应用 automatic
  recall 边界。
- **AC-11 trace 可解释**：测试能够观察当前 session、截止 turn、候选来源 turn、Core 豁免、其他会话放行和
  最终过滤原因。
- **AC-12 既有行为不退化**：048、063、069、086 的检索、压缩恢复、历史查询、有效性与项目规定自动化门禁
  全部通过；默认验证不调用真实外部 LLM。

## 8. 关键信息

- 基础记忆能力：[008-engine-memory](../008-engine-memory/requirement.md)
- 统一检索与自动召回：[048-memory-retrieval-quality-pass-2](../048-memory-retrieval-quality-pass-2/requirement.md)
- 上下文压缩可靠性：[063-context-compaction-reliability](../063-context-compaction-reliability/requirement.md)
- 会话感知历史查询：[069-session-aware-conversation-history-retrieval](../069-session-aware-conversation-history-retrieval/requirement.md)
- 语义替代与 retirement：[086-semantic-supersession-graph](../086-semantic-supersession-graph/requirement.md)
- 相邻需求：097 · context-aware memory reconciliation（098 完成后继续，技术边界独立）
- 本需求分支：feature/098-visible-context-memory-recall-suppression

## 9. 开放问题 / 待技术方案决策

- 自动召回范围的具体类型与命名，以及如何区分“未提供范围”和“已提供当前 session、但截止 turn 为空”。
- 调用方如何把已有 compaction covered_through_uuid 归一化为可比较的来源 turn。
- 普通 Semantic、Episodic 与原始对话候选如何复用现有 provenance 映射来源 turn。
- Semantic 拥有多个当前会话 provenance 时，以哪一个来源 turn 判断资格。
- 已知属于当前会话但来源 turn 无法解析的旧数据如何保守降级。
- 如何在过滤后保留完整 candidate decision trace，并让更旧合格候选正常补位。

## 10. 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-22 | 首次确认；定义完整 visible-source snapshot 与自动召回协同 | 初始范围，已废弃 |
| 2026-08-22 | 第一次修订；改为 context-owned recall frontier，并扩入 context 组装 | 中间范围，已废弃 |
| 2026-08-22 | 根据用户确认收敛为纯 memory 需求：调用方传当前会话截止边界，全部非 Core 自动候选统一过滤，Core 永久召回；仅保留 Conversation 薄接线 | 当前范围 |
