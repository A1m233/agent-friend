# 上下文辅助的 Semantic 纠错

## 状态
CONFIRMED

## 背景

当前长期记忆抽取主要根据本次新产生的对话片段和已展示的 Core / Semantic 判断写入。当目标片段包含“前面搞错了”“不是那个”“我说的是另一个”一类依赖邻近对话的表达时，Extractor 可能无法仅凭当前片段定位被纠正的旧事实、区分原陈述与助手解释，最终只新增正确 Semantic，却没有撤回此前由错误理解产生的旧 Semantic。

086 已提供普通 Semantic 的原子撤回与多对多修正能力，但它解决的是“如何表达和提交 Semantic 变化”，没有规定 Extractor 如何获得用于理解当前纠错表达的邻近对话。

agent-friend 的 Session/Event 是原始会话、active lineage、编辑重发和删除状态的权威来源。memory 已通过 `ConversationFragment` 接收调用方投影后的干净对话素材，不应为了本需求再持久化一份近期原始消息历史。邻近上下文应由调用方根据权威 Session 在观察目标 turn 时提供，memory 只把它作为 Semantic 更新的只读辅助输入。

098 已独立约束自动召回对当前会话压缩边界的使用。本需求位于 memory 写入链路，不调用 098 的召回 API，也不改变自动召回；调用方可以复用同一个有效 compaction 覆盖边界，把压缩后仍保留为原始对话的连续 tail 作为邻近上下文候选。

## 目标

- 让调用方在提交目标对话片段时，同时提供同一会话中与目标相邻的、已经冻结的只读对话上下文。
- 让 Extractor 仅在判断普通 `semantic_changes` 的撤回和替代时使用邻近上下文，能够识别用户纠正和助手对先前错误理解的明确撤回。
- 保持目标片段为唯一的新记忆写入与 provenance 来源；邻近上下文不得被重新抽取或重复写入。
- 不让 memory 复制持久化原始会话历史；重启后由持久化 Session 重新投影相同的有效邻近上下文。
- 让邻近上下文的来源范围、截取结果和 Semantic 纠错结果可以诊断和回归验证。

## 范围

### 包含

- 调用方在目标 turn 完成、准备调用 `Memory.observe` 时，从当前 Session 的 active lineage 选择同会话、位于目标片段之前的已完成用户 / 助手原始文本轮次。
- 调用方复用有效 compaction 覆盖边界确定候选原始 tail：
  - 存在有效边界时，只把边界之后、目标 turn 之前的连续原始历史作为候选；
  - 不存在有效边界时，目标 turn 之前的当前会话有效原始历史均可成为候选；
  - 边界及以前已经由压缩表示承担的旧原文不作为本需求的邻近上下文。
- 调用方在候选 tail 上应用有界的近期完整轮次策略，过滤 partial assistant、工具事件、空消息、inactive 分支和目标片段本身；具体轮数在技术方案阶段确定。
- 调用方在异步 extraction 入队前把选中的文本、角色、时间和 source ref 复制为不可变上下文，worker 不在执行时读取 Session。
- memory 写入契约能够显式区分目标片段与 `semantic_update_context`；没有邻近上下文的旧调用方保持现有行为。
- memory 根据 Extractor 的实际输入窗口对调用方提供的上下文执行最终容量保护；容量保护是模型请求安全措施，不改变调用方对候选范围的业务判断。
- Extractor 仅在当前目标片段明确表达纠正、撤回或更新时，使用邻近上下文理解：
  - 当前表达指向哪段先前对话；
  - 哪条已展示普通 Semantic 因当前纠正而不应继续有效；
  - 当前目标片段是否给出了可靠的替代 Semantic；
  - Semantic 新旧事实的断言来源应归于用户还是助手。
- 用户明确纠正先前事实时，Extractor 能复用 086 的 `semantic_changes.retirements` 撤回相关旧 Semantic，并在目标片段提供可靠替代事实时写入新 Semantic。
- 助手明确撤回自己此前的错误理解时，Extractor 能撤回由该错误理解产生的旧 Semantic；助手仅凭新的自主判断不得推翻用户明确陈述且尚未被可靠证据否定的事实。
- 诊断信息能够区分目标片段和 Semantic 更新上下文，记录目标 turn、上下文 source refs、范围与容量省略、引用的旧 Semantic 及最终提交结果。
- 使用通用化场景覆盖本次真实 bad case 所代表的失败机制：助手此前的错误解释形成了 Semantic，后续目标片段明确承认理解错误并给出正确方向，旧错误 Semantic 应失效。

### 不包含

- 在 memory 数据库中新增近期原始消息历史、复制 Session/Event 或让 memory 成为第二份会话真相源。
- 让 memory 查询 Session、ContextManager、compaction event 或 098 的自动召回结果来补充邻近上下文。
- 修改 098 的 `CurrentSessionRecallScope`、自动召回过滤、普通 Semantic / Episodic / SessionHistorySource 检索、排序或 trace。
- 使用邻近上下文改变 Semantic 检索 query、候选排序或当前有效 Semantic 的展示预算。
- 使用邻近上下文判断或生成 Core 更新、Episodic 条目，或从邻近上下文独立新增普通 Semantic。
- 修改 ContextManager、BuildResult、摘要生成、FIFO、token 预算、工具结果投影或主模型上下文组装顺序。
- 对没有新目标片段或没有新纠错证据的历史记忆做后台扫描、自动纠错、确定性语义去重或推测性修复。
- 改变 086 的 Semantic 变更图、retirement 原子提交、历史谱系或当前有效事实过滤语义。
- 直接理解图片、附件、工具结果或压缩摘要并从中抽取长期记忆；本期邻近上下文只包含调用方提供的已完成用户 / 助手原始文本。
- 跨会话寻找邻近对话。
- 让 silent `memory_only` system turn 使用邻近上下文；其现有 observe 行为保持不变。
- 将异步 extraction 队列升级为持久化队列。进程在已入队任务完成前异常退出导致任务丢失，仍属于现有崩溃语义，不由本需求解决。
- 在 Memory Inspector 中新增纠错谱系或上下文展示界面。

## 关键信息

- 本需求在领域上属于 memory 写入与抽取能力；为避免 memory 复制持久化会话历史，允许在 agent 的既有 `memory_feed` / PostTurn observe 边界增加薄接线，只负责从权威 Session 生成输入，不承载 Semantic 纠错决策。
- 098 与 097 分别位于读写两条链路。098 对 compaction 边界及以前的当前会话非 Core 记忆恢复自动召回资格；097 的调用方把同一边界之后、目标 turn 之前的连续原始 tail 作为邻近上下文候选。两者复用边界事实，但不互相调用。
- “目标片段”是本次 Extractor 被授权写入的唯一新证据；“Semantic 更新上下文”只帮助理解目标片段。上下文中即使存在值得记忆的信息，也不能仅因本次再次出现而产生写入。
- memory 的最终容量保护不得向前跨越调用方给出的候选范围，也不得在容量不足时回查更旧会话；具体完整轮次打包与超限行为在技术方案阶段确定。
- 普通 Semantic 的 retirement 仍必须精确引用 Extractor 输入中实际展示的旧事实，并继续遵守 086 的引用、作用域、原子性和有效性校验。
- 无法唯一确定被纠正对象、纠错主体或替代事实时，应保留旧事实并避免推测性写入。
- 正常重启后，邻近上下文由 Session 的持久化 active lineage 与 compaction checkpoint 重新投影，因此不依赖 memory 进程内缓存。未完成异步任务的崩溃恢复不在本需求范围内。

## 验收条件

- [ ] AC-1：没有有效 compaction 边界时，调用方能够从当前 Session active lineage 中选择目标 turn 之前的近期完整用户 / 助手轮次，并在 `Memory.observe` 前冻结为 Semantic 更新上下文。
- [ ] AC-2：存在有效 compaction 边界时，邻近上下文只来自边界之后、目标 turn 之前的连续原始 tail；边界及以前的旧原文、目标 turn 自身和整个目标 turn 都未包含的其他历史不会进入上下文。
- [ ] AC-3：编辑重发、inactive 分支、partial assistant、工具事件和空消息不会进入 Semantic 更新上下文；同一持久化 Session 在正常重启前后为同一目标位置产生等价上下文。
- [ ] AC-4：异步 worker 只消费入队前冻结的目标片段与上下文，不读取 Session；后续追加对话或压缩不会改变已经入队的 extraction 输入。
- [ ] AC-5：调用方提供的候选轮次超过 Extractor 安全输入容量时，memory 按技术方案规定的完整轮次策略执行容量保护，不超出模型窗口，也不引入候选范围之外的内容。
- [ ] AC-6：目标片段中的用户明确纠正依赖邻近对话才能定位旧事实时，Extractor 能输出对应 Semantic retirement，并在目标片段提供可靠替代事实时输出新 Semantic。
- [ ] AC-7：助手明确撤回自己此前的错误理解，且目标片段、邻近上下文和已展示 Semantic 能确定对应关系时，由该错误理解产生的旧 Semantic 被撤回；无法确定关系时不做推测性 retirement。
- [ ] AC-8：加入或移除邻近上下文不会改变 Semantic 检索 query、Core updates 或 Episodic entries；邻近上下文自身不会独立产生新的普通 Semantic。
- [ ] AC-9：所有新 Core、Semantic、Episodic 和 Semantic change provenance 只包含目标片段来源；上下文 source refs 不进入 observation id、Episodic source 或新事实 provenance。
- [ ] AC-10：没有提供 Semantic 更新上下文的旧入口继续按目标片段执行现有抽取，不查询或持久化额外会话历史，也不会因缺少上下文失败。
- [ ] AC-11：诊断信息能够还原目标 turn、候选边界、选入与省略的上下文 source refs、旧 Semantic 引用、Extractor 输出和提交结果；上下文缺失、歧义未纠错和成功 retirement 可以区分。
- [ ] AC-12：机制性测试覆盖无 / 有 compaction 边界、重启重投影、edit/resend active lineage、冻结输入、容量保护、target/context provenance 隔离、用户纠错、助手自我纠错、歧义保护和 legacy fallback；LLM 评测使用通用示例验证 Semantic 纠错质量。
- [ ] AC-13：098 自动召回、Core-first 抽取、086 Semantic 变更原子性、Episodic 具体性、owner/persona 隔离和 silent `memory_only` observe 行为不退化。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|---|---|---|
| 2026-08-22 | 首次确认目标片段与只读邻近上下文隔离、目标 turn 冻结可见证据依赖及上下文感知纠错边界 | 初始范围，已废弃 |
| 2026-08-22 | 根据 098 最终边界和用户澄清，改为调用方从权威 Session 提供压缩后连续 raw tail；邻近上下文仅辅助 Semantic 纠错，memory 不持久化原始消息 | 当前范围 |
