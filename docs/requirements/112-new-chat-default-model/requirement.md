# 112 · 新对话默认模型

## 状态

CONFIRMED

## 背景

设置页当前把“默认对话模型”当作需要应用到 Bridge 的运行时配置：保存 `chatModel` 后会自动重启整个 Bridge。这个行为把一个桌面端的新对话偏好错误地扩大成了进程级配置变化。

Bridge 重启会更换进程身份，清空进程内的 `SessionModelHistory` 与 cache epoch 状态，并可能等待、打断或影响正在运行的任务。修改一个新对话的默认选择不应制造这些副作用。

桌面对话已经能够在首次发送时携带界面当前选择的完整模型配置，Bridge 也能够用该配置创建新 session。全局默认值因此只需要负责初始化“尚未创建 session 的新对话草稿”，不需要成为 Bridge 的动态运行时状态。

## 目标

- 将“默认对话模型”明确为桌面端新对话草稿的初始模型选择。
- 新草稿创建时复制一次当时的默认模型；之后草稿、已有会话和全局默认值彼此独立。
- 新草稿首次发送时使用界面已经确认的模型配置创建 session，不在发送时重新读取全局默认值。
- 保存默认模型时不重启 Bridge，不改变 Bridge PID、运行中任务、已有会话或 Prompt Cache epoch。
- 设置页标签、说明和成功反馈准确表达“只影响之后新建的对话”。
- 桌面设置不改变 IM、OpenAI/stateless fallback、scheduled task、persona editor 等非桌面新对话入口的默认模型语义。

## 范围

### 包含

#### 新对话草稿初始化

- 应用启动后首次进入新对话区时，用已保存的默认对话模型初始化一份新草稿。
- 用户从已有会话显式创建另一份新对话时，用创建当时的默认对话模型初始化新草稿。
- 新草稿一旦创建，其 pending model 就属于该草稿；之后修改全局默认值不应偷偷改变这份已存在的草稿。
- 离开后再返回同一份尚未发送的草稿属于恢复原草稿，不重新套用全局默认值。
- 用户在新草稿中显式切换模型时，只更新该草稿，不修改设置页保存的默认值。

#### 首次发送与 session 固化

- 新草稿首次发送时，前端必须把该草稿当前展示的完整模型配置显式发送给 Bridge。
- Bridge 使用显式配置创建 session；创建成功后，模型成为该 session 自己的 current model。
- 首次发送不得在临近提交时重新读取全局默认设置并覆盖草稿选择。
- 首次发送已经被接纳后，后续默认设置变化不得改变该 run 的模型。

#### 已有会话隔离

- 已存在 session 继续使用自己的 current model 和 reasoning preset。
- 修改默认对话模型不切换任何已有 session，不增加其 cache epoch，不重建或清空其 `SessionModelHistory`。
- 用户显式切换已有 session 模型时，继续沿用既有的 session 局部切换语义；该行为不修改全局默认值。
- 两个已有 session 的模型、历史和运行时连续性不能被默认设置变化连带影响。

#### 保存与前端反馈

- 设置页将该配置称为“新对话默认模型”，并说明不会切换当前对话或重启服务。
- 保存成功后提示“新对话默认模型已保存”，并说明从下一份新草稿开始使用。
- 保存成功不得显示“对话服务已自动重启”“已应用到 Bridge”或其它不符合实际行为的文案。
- 模型校验或设置持久化失败时，原设置保持有效，界面不宣称保存成功。
- 保存过程中不得调用 Bridge restart 或 supervisor restart。

#### Bridge 与其他入口边界

- 桌面端每次创建新 session 都提供显式模型配置，不依赖 Bridge 的无参默认 fallback。
- Bridge 自己的 server fallback 与桌面默认设置解耦；即使 Bridge 日后因其它原因真正重启，也不能把桌面 `chatModel` 扩散到 IM、stateless、scheduled task 或 persona editor。
- 非桌面入口当前已有的默认模型、显式模型和持久化绑定语义保持不变。
- 真正的 Bridge 重启仍按既有规则开启新的进程/cache epoch；本需求不改变跨进程恢复能力。

### 不包含

- 不实现 Bridge 内的 `RuntimeSettingsState`、`DefaultModelProvider`、热更新 endpoint 或配置中心。
- 不修改 memory model、DeepSeek/Kimi/OpenRouter API Key、Tavily/Amap/QWeather 凭据或它们当前的生效方式。
- 不改变 Voice Bridge 凭据与语音服务生命周期。
- 不修改已有 session 的显式模型切换、reasoning preset、附件能力或并发互斥语义。
- 不为 IM、OpenAI/stateless、scheduled task 或 persona editor 新增模型设置入口。
- 不引入 rollout、跨进程敏感 transcript 持久化或精确 Prompt Cache 恢复。
- 不执行真实 LLM 调用作为本需求的默认验收；机制优先使用 fake/mock 和既有 observation 固定验证。

## 关键信息

- “新对话草稿”指尚未在 Bridge 创建持久 session 的桌面输入状态。
- “创建新草稿”包括应用启动时创建首份草稿，以及用户从已有会话显式开始新对话；仅在窗口或页面间切换后返回同一草稿不算重新创建。
- 全局默认模型、草稿 pending model、session current model、run model snapshot 是四个不同生命周期的状态。
- 相关既有能力：
  - [`../061-session-model-switching/requirement.md`](../061-session-model-switching/requirement.md)
  - [`../102-provider-transcript-cache-reuse/requirement.md`](../102-provider-transcript-cache-reuse/requirement.md)
  - [`../109-prompt-cache-observability-and-optimization/requirement.md`](../109-prompt-cache-observability-and-optimization/requirement.md)
  - [`../110-dynamic-context-cache-continuity/requirement.md`](../110-dynamic-context-cache-continuity/requirement.md)
  - [`../111-agent-run-continuity-and-efficiency/requirement.md`](../111-agent-run-continuity-and-efficiency/requirement.md)

## 验收条件

- [ ] **AC-1 设置语义与文案**：设置页展示“新对话默认模型”及准确说明；保存成功提示只说明之后新建的对话会使用该默认值，不提 Bridge 重启。
- [ ] **AC-2 不重启 Bridge**：保存新的默认对话模型后 Bridge PID、runtime instance id 和 supervisor generation 均不变，且前端没有调用 Bridge restart。
- [ ] **AC-3 新草稿初始化**：设置默认模型后创建的新对话草稿，以保存的模型及默认 reasoning preset 作为初始选择。
- [ ] **AC-4 草稿快照稳定**：一份新草稿创建后，再修改全局默认模型，该草稿的 pending model、文字、附件和引用均不变；下一份新草稿才使用新默认值。
- [ ] **AC-5 草稿局部切换**：在新草稿中显式切换模型只改变该草稿；离开并返回时恢复草稿选择，全局默认设置保持不变。
- [ ] **AC-6 首次发送一致性**：新草稿首次发送携带草稿当前完整模型配置，Bridge 创建的 session current model 与发送前界面展示一致。
- [ ] **AC-7 不在发送时重读默认值**：草稿创建后到首次发送前即使全局默认值发生变化，首次 run 仍使用草稿快照，不被新默认值覆盖。
- [ ] **AC-8 已有会话隔离**：修改默认值时，至少两个已有 session 的 current model、reasoning preset、`SessionModelHistory` generation/provider request count 和 cache epoch 都不变。
- [ ] **AC-9 运行中任务隔离**：默认值在一次 run 进行时变化，该 run 不被中断并继续使用既有 run model snapshot；其他正在执行或排队的任务不被清空。
- [ ] **AC-10 非桌面入口隔离**：修改桌面默认值后，无论 Bridge 是否因其它原因真正重启，IM、stateless fallback、新建 scheduled task 和 persona editor 都不从该桌面设置继承模型。
- [ ] **AC-11 Prompt Cache observation**：修改默认值后，未切模的已有 session 下一次普通请求保持原 cache epoch，并在满足其它前提时继续显示 `strict_extension`；显式 session 切模仍只让目标 session 显示 model-route epoch 变化。
- [ ] **AC-12 保存失败**：模型校验或设置落盘失败时，前端恢复原设置与原文案，不触发 restart，也不显示成功反馈。
- [ ] **AC-13 真正重启边界**：真实 Bridge 重启仍得到新的 runtime identity 和 `process_resume_degraded` 语义；本需求没有伪造跨重启连续性。
- [ ] **AC-14 自动化门禁**：草稿初始化/恢复/首次发送、已有 session 隔离、Bridge fallback 解耦、前端文案和无 restart 调用均有确定性测试，`scripts/check` 全绿。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-31 | 首次确认：默认对话模型只初始化桌面端新对话草稿；首次发送显式固化 session 模型；不重启 Bridge，并与所有非桌面 fallback 解耦 | 新需求 |
