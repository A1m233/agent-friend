# 107 · 活动运行状态与界面投影一致性

## 状态

CONFIRMED

## 背景

[045 · AgentRuntime 统一执行入口](../045-agent-runtime-unified-execution/requirement.md) 已建立统一运行身份、状态与唯一终态；[062 · 桌面多会话并发运行](../062-desktop-multi-session-concurrent-runs/requirement.md) 进一步要求桌面按 session 隔离运行、允许后台继续并在重连后校准。[087 · 上下文压缩进度](../087-context-compaction-progress-feedback/requirement.md)、[091 · 紧凑工具调用活动流](../091-compact-tool-call-activity/requirement.md) 和 [104 · Agent 主动终止命令](../104-agent-controlled-command-termination/requirement.md) 又分别增加了压缩、工具、授权与命令进程的活动表面。

这些能力目前分别维护会话忙碌、消息状态、模型进度、工具状态、授权状态和命令进程状态。各局部表面可以单独正确，却不一定对同一轮运行给出一致结论。[issue 058](../../issues/058-active-run-projection-divergence/README.md) 已记录真实现象：用户切回仍在执行的会话时，侧栏与输入框表示任务正在运行，但最后一条助手消息已经显示完成时间和复制操作，先前完成的命令活动也不再提供“整轮仍在继续”的兜底反馈。

相邻路径还可能在排队、冷启动恢复、push 重连、取消等待、旧事件迟到、终态详情刷新失败、外部 channel 运行和命令状态对账时产生相反投影。用户最终看到的是一个会话，不应被迫理解这些内部状态分别来自哪一层。

本需求建立活动 run、会话、消息和工具活动之间统一的用户可见不变量，让“整轮是否仍在继续”“当前具体在做什么”“是否需要用户处理”“什么时候真正结束”在所有桌面表面保持一致。

## 目标

- 只要某个 session 仍有排队中或运行中的 run，桌面所有相关表面都一致表达该会话尚未结束。
- 消息、工具、命令、压缩或授权等单个活动的完成，不再被误解为整轮 run 已完成。
- 当前没有精确子活动可展示时，仍提供克制、持续且不泄露内部信息的通用活动反馈。
- 会话切换、前端刷新、冷启动、断线重连和乱序事件后，界面能够回到 Runtime 与持久化会话事实一致的状态。
- 正确区分运行完成、失败、取消、跳过、过期和拒绝，不伪造“有新回复”或“处理失败”。
- 用户只能操作当前入口有权控制的 run；外部 channel 占用会话时给出可理解的忙碌反馈，但不提供必然失败的停止入口。

## 范围

### 包含

#### 整轮运行与局部活动的语义边界

- Runtime 的 run ownership 与 terminal 事实决定整轮是否仍在继续；消息或工具局部状态不得单独结束整轮。
- queued、running 和 terminal 在侧栏、当前会话、输入框、活动反馈和操作能力上使用一致语义。
- 同一 run 内可以连续产生多段助手文本、多个模型请求、工具结果或压缩活动；任一中间项完成后，只要 run 未 terminal，界面仍明确表示任务正在继续。
- run 已 terminal 后，界面不再遗留排队、运行、取消中、等待授权或恢复中的旧状态。

#### 消息完成语义与 action bar

- 助手消息能够区分自己是否属于当前 active run，不能仅凭历史投影或局部文本完成推断整轮已经结束。
- 属于当前 active run 的助手消息在 run terminal 前隐藏整个 action bar，包括复制入口与时间。
- 该 run 进入 terminal 后，助手消息才按最终完成、失败或中断结果恢复适用的完整 action bar。
- 更早 run 已经结束的历史消息不受当前新 run 影响，继续保留既有时间与复制能力。
- 当前 run 尚未产生助手消息，或其输出不进入桌面对话时，不修改、占用或伪装最近一条历史助手消息的状态。

#### 单一主要活动反馈

- 当前 session 有 active run 时，始终存在一个可理解的主要活动反馈；已有正文或上一批工具已经完成不能让该反馈消失。
- 需要用户处理的授权、登录或输入请求优先呈现；其次是可操作的运行中命令，再其次是明确的模型、重试或压缩阶段，最后使用通用 queued、继续中或恢复中反馈。
- 同一时刻只自动突出一个主要活动表面，避免 Dock、消息尾部和通用状态同时表达相互竞争的当前阶段。
- 工具活动行和历史详情继续保留审计价值，但不得用某个工具的终态替代 run 的整体状态。
- 对隐藏、ambient、memory-only、voice、IM 或其它不应投影为桌面对话正文的 run，只展示必要的会话级忙碌或恢复反馈，不泄露内部活动内容，也不伪造助手消息。

#### 会话切换、初始化与重连校准

- 从活动会话切走再切回时，已经产生的内容、当前 run 身份和主要活动反馈保持一致；不得出现侧栏运行中、消息已完成的组合。
- 前端首次加载或重新连接 Bridge 时，在活动 run 的初次权威校准完成前，不把受影响会话当成已确认 idle，也不开放会与实际 run 冲突的操作。
- push 断线、应用失焦、WebView 恢复或 REST 对账期间，界面可以进入明确的恢复中状态；不能通过缺失单个事件推断 run 已完成。
- 权威对账中已经不存在的旧 active、pending 或 running 项得到清理；新 push 与旧 pull 交错时，更新、更具体的事实不被旧响应覆盖。
- 缓存中的历史忙碌标记不能脱离当前 run ownership 长期存在，也不能阻止必要的详情刷新和最终收口。

#### 乱序、跨 run 与终态收口

- 同一 run 的旧版本状态不能覆盖新版本状态；已知 terminal 的 run 不能被迟到增量重新激活。
- 同一 session 已经开始更新 run 后，旧 run 的迟到 terminal、详情刷新或传输收尾不得清除新 run 的 active 状态。
- terminal run 先于最后一批增量到达、最后一批增量先于 terminal 到达，以及详情刷新失败时，都能确定性收口到同一最终结果。
- terminal 后的消息和会话状态先依据权威运行事实结束；持久化 session detail 用于补齐最终内容，不是解除 busy 或结束 action bar 隐藏的唯一条件。
- 取消请求被接受但 run 尚未 terminal 时继续表达“正在取消且任务未结束”；只有 terminal 才完成取消收口。

#### 工具、授权与命令状态一致性

- 工具活动摘要、展开详情、授权 Dock、运行中命令 Dock 和侧栏提示对同一调用使用一致的活动或终态事实。
- 持久化工具结果与实时授权、进程快照冲突时，不出现一个表面运行中、另一个表面已结束的长期状态。
- 重连或权威刷新后已经消失的 pending 授权和 running 进程不继续制造“等待授权”或“命令运行中”提示。
- run terminal 时，与该 run 绑定且不允许独立存活的授权和命令活动一并进入可解释终态；不得残留可操作按钮。
- 本需求保持 [091](../091-compact-tool-call-activity/requirement.md) 的紧凑活动和渐进披露边界，不重新设计工具卡片的信息密度。

#### 外部入口、控制能力与后台结果

- voice、IM、系统主动事件或其它正式入口占用当前 session 时，桌面正确显示 busy，并阻止发送、编辑重发、模型切换和其它冲突操作。
- 桌面停止入口只在当前 active run 可由桌面文字入口控制时出现；不可控制的外部 run 给出从原入口处理或等待完成的准确反馈。
- 后台 run 只有在真正产生用户可见新内容时才显示“有新回复”；memory-only、隐藏输出或无可见变化的完成不得制造未读回复。
- failed、cancelled、skipped、expired 和 rejected 按各自语义呈现；取消、跳过、过期或接纳拒绝不得一律降级为“处理失败”。
- 当前正在查看的会话不重复产生后台未读标记；后台结果进入会话后按最新可见内容和终态清除相应反馈。

#### 测试与桌面验收

- 使用确定性 store、projection、组件和 Bridge 测试覆盖运行态、消息态、工具态与到达顺序的组合，不依赖真实 LLM 的随机行为。
- 覆盖当前/后台 session、queued/running/terminal、切换/刷新/重连、用户/voice/IM/主动 run、授权/命令/压缩、取消和详情刷新失败。
- 覆盖旧 terminal 对新 run、terminal 后迟到增量、pull/push 反序和 active run 无桌面对话消息等关键反例。
- 在真实 Tauri 桌面端验收 action bar、主要活动反馈、侧栏、输入框、授权 Dock 和命令 Dock 的组合表现；浏览器预览不能替代桌面端结论。

### 不包含

- 不重做 AgentRuntime 的同 session 顺序、跨 session 调度、取消、预算或唯一终态模型。
- 不实现 Bridge 进程重启后继续执行丢失的内存 run；本期恢复边界是前端/WebView 重载或短暂 push 断线且 Bridge 仍存活。
- 不建设任务中心、运行历史管理器、跨设备控制或多窗口并排 chat。
- 不新增独立于 Agent run 长期存活的后台终端或后台命令生命周期。
- 不实现完整增量 replay、逐 token 无缝补播、百分比、剩余时间或精确阶段预测。
- 不重新设计工具摘要、卡片、参数详情、授权策略、命令执行策略或权限边界。
- 不处理 [issue 057](../../issues/057-assistant-response-phase-projection/README.md) 所属的 commentary/final 输出阶段协议。
- 不改变 IM、voice 等外部 channel 自身的消息或停止 UI；只保证桌面对其 session ownership 的投影与控制边界正确。
- 不为了展示活动状态新增真实 LLM 调用。

## 关键信息

- “消息已完成”只描述一条消息或响应项；“run 已 terminal”才描述整轮任务结束。
- “工具已结束”只描述该工具调用；Agent 仍可能继续模型请求、其它工具或最终整理。
- activity 的精确程度可以随证据降级，但不能消失：精确阶段不可用时退回通用继续中，状态暂时无法确认时显示恢复中。
- action bar 是消息终态操作表面。当前 active run 的助手消息必须整体隐藏，不能只隐藏时间却保留会被误读为完成的其它操作。
- 历史消息与当前运行通过稳定身份关联；不得因为“它是最后一条助手消息”就自动把当前 run 状态附着其上。
- 权威终态与持久化内容是两类互补事实：前者决定是否结束，后者决定最终展示什么。
- 运行状态不写成普通 user/assistant 会话内容；隐藏和内部活动不会因为需要 busy 反馈而进入模型上下文或历史正文。
- 真实桌面验收已纳入本需求最终 AC；执行时继续遵守项目的桌面视觉验收流程与运行态身份门禁。

## 验收条件

- [ ] **AC-1 整轮权威状态**：任一 session 存在 queued/running run 时，侧栏、当前会话、输入框和操作门禁均表达未结束；消息或工具局部完成不能清除 busy。
- [ ] **AC-2 Active 消息 action bar**：属于当前 active run 的助手消息完整隐藏 action bar；run terminal 后按最终结果恢复，更早 run 的历史消息始终不受影响。
- [ ] **AC-3 通用活动兜底**：active run 已有正文、上一批工具已结束或当前没有精确阶段时，仍显示唯一、克制的通用继续中反馈。
- [ ] **AC-4 活动优先级**：等待用户处理、运行中命令、明确模型/压缩阶段和通用活动按确认顺序选择唯一主要表面；不存在相互矛盾或重复争抢焦点的状态。
- [ ] **AC-5 排队状态**：queued run 明确显示等待处理，不伪装成已经运行、完成、失败或可再次发送。
- [ ] **AC-6 会话切回**：运行中的 A 会话切到 B 再切回 A 后，A 的内容、active run、action bar、主要活动反馈和停止能力保持一致，后续增量继续落到 A。
- [ ] **AC-7 初始化门禁**：前端冷启动或 WebView 重载时，在初次 run 校准完成前不会把实际 busy session 开放为 idle；校准后立即进入正确 queued/running/terminal 投影。
- [ ] **AC-8 断线重连**：push 断线期间不伪造完成；重连后权威对账能够清理已消失的 active/pending/running 项并恢复仍有效项。
- [ ] **AC-9 Terminal 确定性收口**：terminal 到达后即结束整轮 busy 与取消中状态；session detail 刷新失败不会留下 streaming 消息、运行中活动或永久隐藏的 action bar。
- [ ] **AC-10 乱序隔离**：旧版本、旧 run terminal、terminal 后迟到增量和反序 pull 不会覆盖或复活较新的 active run；最终投影与最新权威事实一致。
- [ ] **AC-11 工具与命令一致性**：同一工具调用的紧凑摘要、详情、Dock 和侧栏长期状态一致；持久化结果与实时状态冲突时能够对账收敛。
- [ ] **AC-12 授权清理**：已解决、已过期、已取消或权威响应中已不存在的授权不继续显示等待；run terminal 后没有残留可操作授权。
- [ ] **AC-13 控制范围**：桌面文字 run 显示可用停止入口；voice、IM 或其它不可由桌面控制的 run 不显示必然失败的停止操作，并给出准确忙碌反馈。
- [ ] **AC-14 后台结果语义**：只有产生用户可见新内容的后台完成显示“有新回复”；失败、取消、跳过、过期、拒绝和隐藏完成不会被错误归并。
- [ ] **AC-15 无正文运行**：隐藏、ambient、memory-only、voice 或 IM run 没有桌面对话消息时，仍有会话级反馈，但不会篡改最近历史消息或伪造助手正文。
- [ ] **AC-16 多会话隔离**：A/B 并发运行、完成、失败、取消和对账互不污染；一个 session 的旧事件不能改变另一个 session 的消息、活动或操作能力。
- [ ] **AC-17 确定性矩阵**：自动化测试覆盖 active message、排队、切换、初始校准、重连、取消、外部 run、无正文 run、旧 terminal、新 run、迟到事件、授权、命令和详情刷新失败。
- [ ] **AC-18 既有需求不回归**：[062](../062-desktop-multi-session-concurrent-runs/requirement.md)、[087](../087-context-compaction-progress-feedback/requirement.md)、[091](../091-compact-tool-call-activity/requirement.md)、[095](../095-resilient-agent-run-recovery/requirement.md)、[103](../103-session-approval-mode/requirement.md)、[104](../104-agent-controlled-command-termination/requirement.md) 与 [106](../106-message-text-quoting/requirement.md) 的已确认行为继续成立。
- [ ] **AC-19 真实桌面验收**：真实 Tauri 桌面端验证会话切回、active action bar 隐藏、通用兜底、queued、取消、授权、命令、外部 run 和 terminal 收口；结果记录明确使用的运行态与观察路径。
- [ ] **AC-20 项目门禁**：相关 lint、typecheck、单元测试、集成测试与 `scripts/check` 全部通过；不通过时不得进入用户验收或合入。

## 关联上下文

- [issue 058 · 运行中会话的多层状态投影可能相互矛盾](../../issues/058-active-run-projection-divergence/README.md)
- [045 · AgentRuntime 统一执行入口与调度生命周期](../045-agent-runtime-unified-execution/requirement.md)
- [062 · 桌面多会话并发运行](../062-desktop-multi-session-concurrent-runs/requirement.md)
- [087 · 上下文压缩进度与持久记录](../087-context-compaction-progress-feedback/requirement.md)
- [091 · 紧凑工具调用活动流](../091-compact-tool-call-activity/requirement.md)
- [095 · Agent 运行恢复与终止语义](../095-resilient-agent-run-recovery/requirement.md)
- [103 · 会话级授权模式](../103-session-approval-mode/requirement.md)
- [104 · Agent 主动终止命令](../104-agent-controlled-command-termination/requirement.md)
- [106 · 消息文本引用](../106-message-text-quoting/requirement.md)
- [证据索引](./evidence-index.md)

## 变更记录

| 日期 | 变更内容 | 影响范围 |
| --- | --- | --- |
| 2026-08-28 | 首次确认：统一活动 run、消息、工具、授权与命令的桌面投影；补齐初始化、重连、乱序、外部入口和后台 outcome；active run 消息完整隐藏 action bar；真实 Tauri 验收纳入 AC | 新需求 |
