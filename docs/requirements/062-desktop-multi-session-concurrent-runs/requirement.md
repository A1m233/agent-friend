# 062 · 桌面多会话并发运行

## 状态

CONFIRMED

## 背景

[`045-agent-runtime-unified-execution`](../045-agent-runtime-unified-execution/requirement.md) 已让 AG-UI、OpenAI compatible、voice、IM 和系统主动事件进入统一 `AgentRuntime`，并建立“同一 session 串行、不同 session 有界并发”的执行能力。[`046-external-session-sync`](../046-external-session-sync/requirement.md) 又补齐了外部 session 写入后的桌面失效通知和历史校准。

但是，桌面对话仍以单一当前会话持有消息、流式状态和停止控制。用户在某个会话生成期间切换或新建会话时，桌面会主动停止原运行；原会话也无法在后台继续生成。即使直接移除停止调用，旧运行的增量、终态和 busy 状态仍可能写入新会话，造成消息串流、停止错对象和状态互相覆盖。

该缺口已登记为 [`issue 037`](../../issues/037-desktop-multi-session-concurrent-runs/README.md)。它不是单个导航调用的问题，而是桌面端尚未完整承接 Runtime 已具备的多 session 执行模型。

本需求让用户可以在一个会话运行期间自由切换到其他会话，并在不同会话中继续发起对话。运行生命周期、会话内容、草稿和用户反馈均按 session 隔离，同时保持同一 session 的执行顺序、会话持久化和跨入口协调语义。

## 目标

- 用户切换或新建会话时，已经被系统接受的原会话运行继续在后台执行，不因导航动作被取消。
- 不同 session 可以在 Runtime 的有界并发范围内独立运行；一个慢会话不阻止用户在其他会话继续对话。
- 每个 session 的消息、运行状态、停止操作、错误、模型和草稿互不污染，迟到事件不能覆盖其他会话的状态。
- 桌面能够展示后台会话的运行、完成未查看和失败状态，并在用户返回时恢复可靠的内容视图。
- 用户显式停止文字运行时只影响目标 session 的目标 run，并以权威终态确认停止完成。
- 桌面短暂丢失流连接后，Bridge 进程内已接受的运行继续执行；桌面重连后能用运行状态和持久化会话内容恢复一致视图。
- IM、voice 和其他正式入口继续服从同一 session 的运行互斥；桌面不会因不知道外部运行状态而向忙碌会话发起冲突操作。
- 会话草稿形成统一、可扩展的 session 级工作区，首期覆盖输入文本、临时附件及其准备状态，为未来增加其他输入资产保留清晰边界。

## 范围

### 包含

#### 多会话并发与导航连续性

- 桌面对话允许多个不同 session 同时处于已接纳、等待或运行状态，实际并发量继续服从 Runtime 的统一上限。
- 用户从运行中的会话切换到历史会话、进入新会话首页或在其他会话发起新消息时，原运行继续执行。
- 同一 session 仍只允许一轮桌面交互运行处于活动状态；运行结束前不允许桌面再次发送、编辑重发或继续工具结果。
- 达到 Runtime 并发上限时，已接纳但尚未执行的运行具有明确等待状态，不得被桌面误报为已经完成或失败。
- 任意 run 的增量、终态、错误和清理只作用于其所属 session 和 run；旧 run 的迟到收尾不得清除另一个 run 的 busy 状态。

#### 运行生命周期与精确停止

- Bridge 进程内每个已接纳 run 都具有可查询、可通知的身份、所属 session、来源 channel、生命周期状态和终态结果。
- 桌面展示的运行状态以 Bridge/Runtime 的事实为准；本地乐观状态只能用于缩短反馈延迟，不能成为第二份权威运行状态。
- 用户在文字对话中点击停止时，只停止当前 session 中由桌面文字入口发起的目标 run。
- 停止请求被接受不等于运行已经结束；桌面应等待目标 run 进入 interrupted/cancelled 终态后再完成状态收尾。
- 关闭、替换或短暂丢失某条桌面流连接不自动表达用户停止意图。只有显式停止、Runtime 关闭或其他已定义的取消来源才能取消运行。
- voice、IM 等具有专属停止或收尾流程的外部 run 可以使桌面 session 呈现 busy，但桌面文字停止按钮不得绕过其 channel 语义直接终止它们。

#### Session 级消息与视图恢复

- 每个 session 独立保留当前可见消息、partial assistant 内容、模型、加载状态、运行状态和错误状态。
- 用户切回仍在运行的 session 时，能够看到该 session 已产生的内容并继续观察后续变化。
- 后台 run 完成、失败或取消后，桌面通过持久化 session detail 完成最终校准；Session JSONL 继续是会话内容事实源。
- 流连接断开期间可以不补播逐 token 动画，但重连后不得丢失已经成功持久化的消息、工具结果和最终内容。
- session detail、运行终态和实时增量并发到达时，旧请求、旧 revision 和旧 run 不能覆盖更新状态。
- 应用重新聚焦、chat WebView 恢复或 push 重连后，桌面能够重新校准已加载 session 的活动状态和当前会话内容。

#### Session 级统一草稿工作区

- 不同 session 的输入文本和临时附件草稿分别保存；切换会话不会把 A 的草稿带到 B，也不会清空 A 尚未发送的内容。
- 草稿统一包含本次待发送输入所需的用户资产及准备状态，首期至少覆盖文本、附件引用、上传/准备进度、失败状态和模型兼容状态。
- 用户返回会话后，仍在当前应用生命周期内有效的草稿恢复到离开前状态；附件失败或与当前模型不兼容时继续展示原草稿并给出可操作反馈。
- 草稿管理应允许未来增加新的输入资产或准备步骤，而不要求各输入组件分别维护彼此不可见的 session 状态。
- 尚未首次发送的新会话首期只保留一个 home draft；本需求不创建多个不可导航、未持久化的临时会话。
- 草稿工作区首期只保证当前应用生命周期内的切换恢复，不承诺应用重启后恢复临时附件或未发送输入。

#### 侧栏状态与后台完成反馈

- 会话侧栏能够区分至少正在处理、完成未查看和失败状态，且状态与对应 session 绑定。
- 当前正在查看的会话正常展示流式内容，不额外制造重复的完成提醒。
- 后台会话完成或失败后，用户无需猜测即可从侧栏发现；进入会话后相应未查看状态被清除。
- 多个后台会话同时结束时，反馈不能形成大量遮挡当前操作的提示；侧栏状态是持续可回看的主要入口。
- 会话列表刷新、标题更新和后台完成状态并发时，运行标记不得因列表对象被替换而丢失或串到其他会话。

#### 跨入口 busy 与会话 mutation 安全

- 桌面能够识别由 AG-UI、OpenAI compatible、voice、IM 或其他正式 Runtime 入口造成的 session 活跃状态。
- 同一 session 存在外部活动 run 时，桌面阻止发送、编辑重发、继续工具结果、模型切换和其他会改变运行语义的操作，并展示可理解原因。
- 正在运行或排队的 session 不允许被归档、删除或执行会破坏运行一致性的 mutation；前端预防和后端权威校验必须同时存在。
- 重命名等不改变对话执行语义的展示元数据操作可以继续进行，但不得干扰运行状态或内容校准。
- 外部 channel 完成后，既有 session changed 机制继续负责内容与列表校准，不复制第二套会话事实源。

#### 失败、断线与资源边界

- 一个 session 的网络错误、取消、provider 失败或本地投影失败不改变其他 session 的运行和界面状态。
- chat 流连接断开后，Bridge 必须继续有界地消费或保存运行事件，不能因为无人读取而永久阻塞 Runtime worker。
- 重连后的状态恢复必须能区分运行仍在等待、仍在执行、已经完成、已经失败和已经取消。
- Bridge 或桌面关闭时，现有 Runtime drain 和资源清理继续形成明确结果，不把仍在执行的工作伪装成已完成。
- 运行状态通知、内容失效通知或后台缓存均有明确容量和清理边界，不随历史 run 数量无限增长。

#### 测试与真实桌面验收

- 使用可控 fake/mock LLM 和 tool 覆盖跨 session 并发、同 session 互斥、等待、停止、失败、断线、重连和乱序终态。
- 覆盖 A/B 会话快速切换、旧 run 迟到收尾、后台 detail refresh、草稿隔离和会话列表状态刷新等前端竞态。
- 覆盖桌面流断开后运行继续、重新校准最终内容，以及停止目标 run 不影响其他 run 的 Bridge 集成测试。
- 在真实 Tauri 桌面端验证会话切换、并发发送、侧栏运行/完成状态、独立停止、草稿恢复和后台完成反馈。
- 浏览器或 Vite 预览只能辅助开发，不能替代本需求的真实桌面运行与交互结论。

### 不包含

- 不支持同一 session 内两个 Agent run 并行执行；同 session 继续严格串行。
- 不实现应用进程或 Bridge 进程重启后继续执行未完成 run。
- 不实现通用的持久化增量事件日志、完整 SSE cursor replay 或逐 token 无缝补播。
- 不支持多个未首次发送、未持久化的新会话草稿，也不承诺草稿跨应用重启恢复。
- 不新增多窗口 chat 或多设备同时控制同一 run 的能力。
- 不允许桌面文字页直接停止 voice、IM 或其他 channel 的专属运行。
- 不新增手工调整 Runtime 并发上限、重排等待队列或管理历史 run 的高级 UI。
- 不改变 LLM provider、工具执行、context compaction、memory observe 或 session JSONL 的领域语义。
- 不把运行状态写成普通 user/assistant 会话消息，也不把运行通知变成第二份会话内容源。
- 不新增系统级桌面通知；后台结果首期以会话侧栏状态和当前应用内反馈为主。
- 不扩展 agent-cli 的交互或运行恢复能力。

## 关键约束

- `AgentRuntime` 继续拥有同 session 顺序、跨 session 有界并发、取消和统一终态；桌面不得复制第二套调度器。
- Session JSONL 继续拥有会话内容事实；运行状态是独立的进程内事实，两者通过稳定的 session/run 身份关联。
- 导航、订阅和运行生命周期是不同语义：离开页面不等于取消，停止订阅不等于停止运行。
- 前端所有异步事件和请求提交前必须验证目标 session 与 run 身份，不能以“当前页面是谁”隐式决定写入目标。
- 运行状态、通知和草稿缓存必须有显式清理规则，terminal run 和失效草稿不能无限驻留。
- 现有 045 的客户端断开取消语义需要按本需求的新产品边界重新评估；只改变桌面 AG-UI 所需范围，不外推到 voice、IM 或 OpenAI compatible 的既有收尾。
- 自动化测试优先使用 fake/mock；如需真实 LLM 并发观察，执行前遵守 `llm-api-confirm`。

## 验收标准

- **AC-1 导航不中断**：A 会话流式生成期间切到 B 或新会话，A 不被取消；用户可以在 B 发起运行，A/B 在 Runtime 限额内独立推进。
- **AC-2 消息与状态隔离**：A/B 的文本增量、工具状态、错误、busy 和 terminal 只更新各自 session；任一旧 run 的迟到收尾不能清除或覆盖另一 run。
- **AC-3 同 Session 互斥**：同一 session 存在 waiting/running run 时，桌面发送、编辑重发和继续工具结果均被阻止；后端仍保持同 session 严格串行。
- **AC-4 切回活动会话**：用户返回仍在运行的 session 后能看到已产生的 partial 内容并继续接收变化，不重复插入 user 消息或 assistant 占位。
- **AC-5 精确停止**：停止 A 的桌面文字 run 只取消指定 session/run；B 和其他 channel 不受影响，A 最终以 interrupted/cancelled 权威终态收尾。
- **AC-6 等待与权威状态**：并发上限已满时，新 run 显示为等待；queued、running、completed、failed 和 cancelled 能经查询或通知恢复，前端乐观状态不会长期偏离 Runtime。
- **AC-7 后台完成反馈**：非当前 session 完成或失败后，侧栏显示对应状态；进入会话后内容与 Session JSONL 一致，未查看标记按约定清除。
- **AC-8 短暂断线恢复**：chat SSE/WebView 短暂断开不取消 Bridge 进程内已接纳 run；恢复后能够校准活动状态和已持久化消息，最终内容不丢失。
- **AC-9 Session 级草稿**：A/B 分别存在文本和临时附件草稿时，来回切换后各自内容、准备状态和兼容状态保持；发送或移除只作用于目标 session。
- **AC-10 Home 草稿边界**：尚未首次发送的新会话只维护一个 home draft；首次发送建立 session 后，该草稿正确归属新 session，不污染其他会话。
- **AC-11 外部入口协调**：voice、IM 或其他正式入口占用某 session 时，桌面正确显示 busy 并阻止冲突操作；桌面停止按钮不能绕过对应 channel 的专属收尾。
- **AC-12 Mutation 安全**：运行或等待中的 session 不能被归档、删除或执行不安全语义 mutation；前端绕过时后端仍返回稳定的 session busy 结果，重命名不影响运行。
- **AC-13 失败隔离与容量**：一个 run 的断线、取消、provider 错误和投影失败不影响其他 run；运行状态、事件缓冲和前端缓存均能在 terminal 后有界清理。
- **AC-14 乱序与同步竞态**：session changed、detail refresh、流事件、terminal 和会话导航乱序到达时，最终界面与目标 session 的最新运行和持久化事实一致。
- **AC-15 真实桌面验收**：真实 Tauri 桌面端中，会话切换、并发发送、侧栏状态、独立停止、草稿恢复和后台完成反馈均可理解、可操作且不阻断当前输入。
- **AC-16 自动化与门禁**：Runtime/Bridge 生命周期、显式停止、断线恢复、跨入口 busy、前端 session cache、草稿工作区、侧栏状态和关键竞态均有自动化测试，`scripts/check` 全绿。

## 相关资料

- 问题登记：[`../../issues/037-desktop-multi-session-concurrent-runs/README.md`](../../issues/037-desktop-multi-session-concurrent-runs/README.md)
- 统一运行入口：[`../045-agent-runtime-unified-execution/requirement.md`](../045-agent-runtime-unified-execution/requirement.md)
- 外部会话同步：[`../046-external-session-sync/requirement.md`](../046-external-session-sync/requirement.md)
- 多模态附件：[`../059-multimodal-message-attachments/requirement.md`](../059-multimodal-message-attachments/requirement.md)
- Composer 附件交互：[`../060-composer-attachment-inputs/requirement.md`](../060-composer-attachment-inputs/requirement.md)
- 会话模型切换：[`../061-session-model-switching/requirement.md`](../061-session-model-switching/requirement.md)

## 开放问题

- Bridge 进程内运行状态需要保留到什么时点、terminal 状态如何有界清理，以及重连校准需要哪种最小查询/通知能力，由技术方案结合现有 Runtime handle 与 push 通道确定。
- 桌面流订阅断开后如何持续消费运行事件并避免 worker 背压，同时不提前实现完整 replay，由技术方案确定。
- Session 级草稿工作区如何统一文本、附件准备状态和未来输入资产，并与上传 draft 的有效期、发送消费和模型兼容检查配合，由技术方案重点设计。
- 侧栏的运行中、完成未查看和失败状态如何与现有标题、更新时间、hover action 共存，需要在真实桌面布局下确定。
- 外部 channel 的 busy 信息需要暴露到什么粒度，以及哪些 metadata 可用于展示而不泄漏内部实现，由技术方案确定。
- 045 中已有的 consumer abandoned 取消行为如何迁移且不破坏其他协议入口，需要结合 AG-UI encoder、RunHandle 和 Runtime shutdown 测试明确兼容边界。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|---|---|---|
| 2026-07-25 | 初版确认：桌面支持应用/Bridge 存活期间的多 session 并发；运行状态由 Bridge/Runtime 持有；导航和订阅不再隐式取消；支持精确停止、侧栏状态、跨入口 busy、session 级统一草稿和短暂断线校准；跨进程恢复与完整事件 replay 不包含 | Phase 1 |
