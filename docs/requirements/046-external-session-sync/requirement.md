# 046 · 外部会话更新同步

## 状态

已完成（Completed）

## 背景

桌面 chat 当前把持久化 session 作为事实来源，但前端只在打开会话、主动发送结束或打开历史列表等局部时机重新拉取数据。当 QQ/IM、voice、系统主动轮、其他窗口或未来外部入口更新同一个 session 后，chat 不会及时得知缓存已经失效，可能持续显示旧消息、旧摘要和旧排序，直到用户手动切换会话或触发其他刷新动作。

[045 · AgentRuntime 统一执行入口与调度生命周期](../045-agent-runtime-unified-execution/requirement.md) 已让 AG-UI、OpenAI compatible / voice、IM 和系统主动事件进入统一运行控制面，但统一执行入口本身不等于前端缓存同步：会话内容、会话元数据和异常运行中已经成功持久化的部分变化，仍需要一个覆盖所有生产写入入口的失效通知机制。

[issue 035](../../issues/035-external-session-updates-not-reflected/README.md) 已记录这一问题。本需求将其按多入口共享的同步模型独立设计和交付，而不是只为 QQ 增加局部通知。

## 目标

- 任意生产入口成功改变持久化 session 后，桌面 chat 能及时获知对应 session 已失效。
- 当前打开的 session 变化后，chat 自动同步最新持久化消息；非当前 session 变化后，会话列表及时更新排序、摘要、标题、状态和时间等可见信息。
- 前端继续把持久化 session 当作事实来源，通过“push invalidation + pull refresh”恢复一致性，不承担完整消息事件的增量合并。
- 自动同步与本地 chat streaming、并发请求和用户阅读状态安全共存，不以缓存刷新覆盖进行中的生成或打断用户操作。
- push subscriber 在 agent bridge 暂不可达或 SSE 断开后能够自动恢复，并在恢复后校准可能漏掉的 session 状态。
- 新机制覆盖现有及未来经正式持久化边界写入 session 的生产入口，避免协议入口各自补发通知而再次产生遗漏。

## 范围

### 包含

1. **统一 session changed 失效通知**
   - session changed 表达“某个持久化 session 已发生变化”，不携带完整消息内容。
   - 通知至少能够关联 `session_id`、持久化 freshness/revision 和触发来源等诊断所需的最小信息。
   - freshness 标识必须能在重复通知、短时间连续写入和异步刷新乱序时判断新旧；不能把仅在单次 subscriber 连接内有效的序号当作持久化版本。
   - 通知在影响 session detail 或 session summary 的生产写入成功后产生，包括新建、内容追加、展示元数据变化、归档状态变化和删除等相应变化。
   - 通知丢失不影响持久化正确性；持久化 session 始终是真相源。

2. **覆盖所有生产写入入口**
   - AG-UI/chat、OpenAI compatible / voice、QQ/IM、系统主动轮、会话管理操作、其他桌面窗口以及未来复用正式 session 持久化能力的入口，均由同一所有权边界产生失效通知。
   - 不要求各协议 adapter 分别记住发送通知，避免新入口或异常分支漏发。
   - 机制自然兼容 045 已完成的统一 `AgentRuntime` 执行入口及同 session 串行约束。

3. **会话列表与当前会话分别刷新**
   - 已加载的 session list 收到任意相关 session 变化后，能够更新受影响的排序、摘要、时间、标题、归档状态或删除结果。
   - 当前打开的 session 收到自身变化后，能够重新拉取 detail 并显示最新持久化消息。
   - 非当前 session 的变化不触发无意义的 detail 拉取。
   - list refresh 与 current-session detail refresh 可独立调度、独立失败和独立恢复。

4. **通知与请求并发治理**
   - 同一 session 的重复通知和短时间连续写入能够合并，避免为每条持久化事件发起一次网络请求。
   - 通知乱序、请求返回乱序或旧请求延迟返回时，旧结果不得覆盖已知更新的状态。
   - session 切换、chat 页面卸载或对应订阅失效后，先前请求的返回不得写入已经失效的视图。
   - 单次 refresh 失败时保留已有可用缓存，并允许后续通知或连接恢复再次校准。

5. **本地 streaming 冲突保护**
   - 当前 session 正在本地 streaming 时，自动 detail refresh 不得替换乐观 user message、assistant 占位或仍在累积的增量内容。
   - streaming 期间到达的当前 session 失效通知会被记录和合并，在本地运行进入完成、失败、取消或中断等终态后再校准持久化 detail。
   - chat 自己产生的 session changed 通知与外部入口通知遵循同一去重和刷新规则，不依赖为 chat 添加特殊旁路。
   - refresh 后的持久化投影不得造成消息重复，也不得把较旧 pull 结果覆盖到较新的本地或持久化状态上。

6. **用户阅读与输入状态保护**
   - 自动 refresh 不进入“重新打开会话”的清空或全屏历史 loading 流程。
   - 自动 refresh 不清空、提交或转移当前 composer 输入，不抢占输入焦点。
   - 用户位于消息底部时，新增内容可以保持贴底体验；用户正在阅读历史时，不自动跳到底部。
   - detail 替换应尽量保持现有滚动位置或阅读锚点；遇到历史 rewrite 导致原锚点消失时允许退化到相邻稳定位置，但不得粗暴重置阅读状态。

7. **subscriber 断线恢复与兜底校准**
   - push subscriber 在启动时 agent bridge 暂不可达、HTTP/SSE 建连失败、网络错误、服务端关闭连接或 stream 自然结束后自动重连。
   - 重连采用有上限的退避等待，避免紧密重试；在桌面应用存活期间持续尝试，直到连接成功或应用退出。
   - 断线后的首次成功重连会触发一次恢复校准：刷新已加载的 session list，并刷新当前打开的 session。
   - 恢复校准复用正常 session changed 的调度、合并、streaming 延后和防乱序机制，不建设第二套刷新路径。
   - 多次快速断开/重连不会导致无界刷新或重复覆盖。

8. **可诊断性与测试**
   - 后端、Tauri subscriber 和前端刷新调度的关键日志能够关联 session、revision/source、连接恢复和 refresh 是否被合并、延后或丢弃。
   - 后端提供覆盖 session changed 产生边界和主要生产写入类型的自动化测试。
   - Tauri subscriber 提供覆盖事件解析、目标窗口分发、有限退避、断线重连和恢复信号的自动化测试。
   - 前端 store 提供覆盖 list/detail 分流、高频合并、重复和乱序通知、请求乱序、组件失效、streaming 延后、滚动策略信号及重连校准的自动化测试。

### 不包含

- 前端解析并增量合并完整 conversation event stream。
- 在 session changed 通知中携带完整消息、完整 session detail 或完整列表摘要。
- 为单一 QQ/IM adapter 增加无法被其他入口复用的专用同步补丁。
- 配置保存后的 agent bridge 或 voice 服务自动重启；该能力仍由 issue 031 后续处理。
- 通用服务生命周期管理框架、跨服务 supervisor 或任意后台任务恢复框架。
- 持久化 SSE 消费游标、服务端历史通知重放、跨设备同步或分布式消息系统。
- 新的通用消息中心、未读计数产品体系或前端任务中心。
- `agent-cli` 的同步机制、Runtime 迁移或新增维护投入。
- issue 032 所登记的旧 LLM 脚本凭证读取迁移。
- 真实 LLM/provider API 调用；如后续确需观察型验证，必须另行取得用户明确授权。

## 需求

### R-1 · 持久化事实与通知语义

- **R-1.1**：session changed 必须是失效通知，而不是第二份 session 数据源。
- **R-1.2**：只有相关持久化写入成功后才能对外声明 session 已变化；写入失败不得产生虚假的新 revision。
- **R-1.3**：session 的 detail、summary 和展示元数据仍通过既有读取接口获取，通知负载保持最小化。
- **R-1.4**：持久化 revision 必须能跨 subscriber 重连继续比较 freshness；连接内递增序号只能用于诊断连接丢帧，不能替代该 revision。

### R-2 · 生产入口完整覆盖

- **R-2.1**：所有正式 session 内容和元数据写入路径必须共享统一的通知所有权边界。
- **R-2.2**：新增生产入口只要复用正式持久化能力，就应自然获得 session changed 行为，不要求协议层额外补发。
- **R-2.3**：异常或取消运行中已经成功落盘的可见变化也必须能够使前端缓存失效。
- **R-2.4**：045 的统一 Runtime 继续负责运行生命周期和同 session 顺序；本需求不得复制第二套运行调度。

### R-3 · 刷新调度与一致性

- **R-3.1**：list refresh 和 current-session detail refresh 必须按各自需要独立调度。
- **R-3.2**：同一 session 的高频通知在短时间窗口内合并，但最终不得遗漏最新 revision 的校准。
- **R-3.3**：任何 refresh 结果只有在其目标 session、页面生命周期和 freshness 仍有效时才能提交到 store。
- **R-3.4**：较旧请求结果不得覆盖较新请求结果；较旧 revision 的通知不得触发状态回退。
- **R-3.5**：暂时拉取失败不清空已有可用数据，后续通知或重连校准可继续恢复一致性。

### R-4 · Streaming 安全

- **R-4.1**：当前 session 本地 streaming 期间禁止用自动 pull 结果整体替换进行中的前端消息状态。
- **R-4.2**：streaming 期间收到的 invalidation 必须合并为待校准状态，并在本地 streaming 进入终态后执行。
- **R-4.3**：本地 streaming 的终态收尾与失效刷新并发时，必须由 freshness 和请求代次共同防止旧 pull 覆盖新状态。
- **R-4.4**：同步机制不得改变既有 stop、error、interrupted 和 edit-resend 的用户可见语义。

### R-5 · 用户交互连续性

- **R-5.1**：自动 refresh 不得清空 composer 草稿、改变焦点或阻止用户继续输入。
- **R-5.2**：用户正在阅读历史且视图不处于贴底状态时，自动 refresh 不得强制滚到底部。
- **R-5.3**：用户处于贴底状态时，新内容到达后应保持自然贴底。
- **R-5.4**：自动 detail refresh 与用户主动打开另一 session 必须有清晰优先级；旧 session 的返回不得污染新 session。

### R-6 · 连接恢复

- **R-6.1**：subscriber 对首次建连失败和已连接后的断流使用同一自动恢复策略。
- **R-6.2**：重连等待时间有明确上限且避免热循环；重试持续到成功或桌面应用退出。
- **R-6.3**：断线后的成功重连必须触发 list 与当前 session 的一次兜底校准，以弥补断线期间无法获知的变化。
- **R-6.4**：恢复校准必须复用正常 refresh 调度，不绕过 streaming、去重、防乱序和页面生命周期保护。
- **R-6.5**：本需求不得借连接恢复引入配置保存自动重启或通用服务生命周期框架；后续 issue 031 直接复用本需求提供的断线恢复能力。

## 验收标准

- **AC-1 · 当前 Session 自动同步**：当前 chat 打开 session A 时，从 IM、voice、系统主动轮或另一正式入口更新 A；不切换会话即可看到最终持久化消息，且通知负载不包含完整消息。
- **AC-2 · 非当前 Session 列表同步**：当前打开 A 时更新 session B；A 的 detail 不被拉取或替换，会话列表中的 B 能更新排序、摘要、时间及其他受影响字段。
- **AC-3 · 生产入口覆盖**：自动化测试证明 AG-UI/chat、OpenAI/voice、IM、主动轮及会话管理相关生产写入共享通知边界；新增基于正式持久化能力的测试入口不需要协议层手动补发。
- **AC-4 · Revision 与重复通知**：同一 revision 的重复通知不造成重复状态更新；连续多个 revision 被合并后，最终显示最高已知 revision 对应的数据；重连后仍可判断持久化 freshness。
- **AC-5 · 高频合并**：同一 session 短时间连续写入不会线性触发 detail/list 网络请求，合并窗口结束后仍能校准到最新事实。
- **AC-6 · 请求乱序**：人为控制两个 refresh 反序返回，旧响应不能覆盖新响应；切换 session 或卸载 chat 后，旧请求返回不写入当前视图。
- **AC-7 · Streaming 保护**：当前 session 本地 streaming 时收到一个或多个 invalidation，乐观 user message 和 streaming assistant 内容不被替换；stream 终态后只执行必要的合并校准，并与持久化投影一致。
- **AC-8 · 阅读与输入保护**：自动 refresh 不清空 composer、不抢焦点；用户阅读历史时不强制跳底，原阅读位置或相邻稳定位置得到保留；贴底用户在新内容到达后保持贴底。
- **AC-9 · 初次不可达恢复**：桌面端先于 agent bridge 启动时，subscriber 按有限退避持续重连；bridge 可用后无需重启桌面端即可建立订阅并触发一次兜底校准。
- **AC-10 · SSE 断流恢复**：已连接后中断 SSE，并在断线期间更新 session；重连成功后 list 和当前 session 被校准，断线期间的更新最终可见。
- **AC-11 · 重连风暴治理**：连续失败和快速断开/重连不会形成热循环、无界任务或无界 refresh；恢复刷新仍满足合并、streaming 延后和防乱序要求。
- **AC-12 · 失败保留旧状态**：detail 或 list refresh 暂时失败时保留原缓存；下一次通知或重连恢复后能够成功校准。
- **AC-13 · 机制性测试**：后端事件、bridge push 端点、Tauri subscriber 和前端 stores 的相关自动化测试全部通过，测试不调用真实 LLM/provider API。
- **AC-14 · 全量门禁与用户验收**：`./scripts/check` 全绿；在用户明确确认用户可见行为验收完成前，需求不得标记完成，issue 035 不得标为 `resolved`，分支不得合入 `main`。

## 约束与兼容性

- 单设备、单 agent bridge 进程仍是当前产品假设；不扩展为跨主机一致性协议。
- Session JSONL 及其配套 metadata 继续承载持久化事实；前端 store 只是可恢复缓存。
- 新机制应沿用现有 REST detail/list 读取语义，不要求前端消费 Runtime 的完整 conversation events 来恢复 session。
- 通知和 refresh 故障不得反向影响 Agent 运行或持久化写入结果。
- 不为后续 issue 032 或 issue 031 的串行开发保留分支内兼容层；issue 031 可直接依赖本需求已经合入 main 的 subscriber 恢复能力。
- CLI 不在本需求范围内，保留其现状但不增加同步能力或兼容投入。
- 自动化验证使用 fake/mock LLM、provider 和网络时序控制；任何真实外部 LLM API 调用须另行获得明确授权。

## 开放问题

- 持久化 revision 的具体存储位置、生成时机，以及内容 JSONL 与展示 metadata 如何共享同一 freshness 空间，由 `design.md` 基于现有持久化结构确定。
- session changed 是否扩展现有 push envelope kind、使用独立 envelope，或由 bridge 内部事件总线映射到 SSE，由 `design.md` 决定；无论选择何种形态，都必须保持失效通知语义。
- list refresh 是始终全量拉取，还是在不改变前端简单 pull-cache 模型的前提下允许服务端提供单 session summary 读取，由 `design.md` 评估；本需求不要求前端做复杂增量合并。
- 自动 refresh 的滚动锚点实现和必要的新内容提示形态，由 `design.md` 在不引入完整未读体系的前提下确定。
- 重连退避的初始值、倍率、上限、抖动和可测试注入方式由 `design.md` 确定。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|---|---|---|
| 2026-07-12 | 初版需求确认：建立覆盖所有生产写入入口的 session changed 失效通知，以 push invalidation + pull refresh 同步当前 session 和会话列表；处理高频合并、重复/乱序、streaming、阅读状态和页面生命周期。 | 新需求 |
| 2026-07-12 | 纳入 subscriber 首次不可达与 SSE 断流后的有限退避持续重连；重连后复用正常刷新机制校准 list 和当前 session；明确不实现配置保存自动重启或通用生命周期框架。 | 范围补充 |
