# 015 · 桌宠气泡与前端对话 Owner（desktop pet bubble + frontend conversation owner）

> Desktop Pet Bubble & Frontend Conversation Owner
>
> 把 agent 编排层 014 已经定稿的 bridge push 通道**在桌面端消费侧落地**——让 chat / pet 两个 webview 共享同一份 conversation 事件流，pet 窗以 speech bubble 作为主对话载体，主动轮（bedtime / idle reflection 等）真正"出现"在桌面上。本期是 [`desktop-completeness`](../../explorations/desktop-completeness/) §4 "桌宠侧消息呈现 + 跨窗口订阅 + bridge push 通道" 三件捆绑里的**桌面端剩余两件**封口（push 通道侧已由 014 完成）。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

[需求 014](../014-engine-main-loop-and-bridge-push/) 已经把 agent 编排层 `AgentRuntime` 调度内核、`EventSource` 抽象、Hook 体系、以及 `agent-bridge` 的 agent→桌面 push 通道全部落地，并配 dev CLI 端到端验证通过。但**桌面端消费侧缺位**：

- `frontend/src/pages/pet/App.tsx` 全文不到 50 行，pet 窗只做拖拽、占位形象、调 `open_chat` invoke 弹 chat 窗三件事；**conversation 事件流（user / assistant / tool）完全跟 pet 无关**——它们只在 chat 窗的 `useConversationStore` 里被消费、只在 `MessageList` 里被渲染。
- pet 窗没有订阅 014 的 push 通道；bedtime / idle reflection 这类主动轮即使在后端跑出来，桌面上**没有承接位**。
- 用户必须打开 chat 窗才能看到 agent 说什么 → 桌宠等于一个**带形象的启动器**，跟所有调研对象（clawd / TinyRoommate / NekoAI / Codex Pet）的"桌宠本身就是主对话载体"模式不符（详见 [`desktop-pet-form-factor`](../../explorations/desktop-pet-form-factor/) §1）。

详细推导见 [`desktop-completeness`](../../explorations/desktop-completeness/) §3–§4；本需求**不复述**。

### 1.2 这次要做什么

按 [`desktop-completeness`](../../explorations/desktop-completeness/) §4 Tier 0 桌面端剩余两件 + 014 §3 "桌面端 Tier 0：本期协议出口已定，前端消费侧留下个需求" 落地：

- **前端 conversation 事件 owner**：新增一层 owner 抽象，chat / pet 两个 webview 共享同一份事件流；现有 `runAgentStream` / `applyAguiEvent` reducer / `useConversationStore` **保留**，owner 在已有数据通路上**插一层**，不重写。
- **pet 窗 speech bubble UI**：新增气泡组件作为**主动轮主出口**，按 policy 显示事件。
- **接入 014 push 通道**：owner 同时订阅"user 触发流"和"agent 主动轮流"，按事件来源走不同 UI 分发路径。
- **事件 → 出口分发机制**：owner 不锁死"哪种事件去哪个出口"的具体规则，把"事件 → 出口"做成一层可替换的 policy（design 阶段定第一版默认）。
- **跨 Space / 全屏浮动**：纯 Tauri 配置，让主动轮气泡冒出来时不被 chat 窗或全屏 app 遮挡，保证"用户看得见"语义成立。
- **dev CLI 端到端**：复用 014 dev CLI 的触发链，验证 bedtime 真的在桌宠头上冒气泡、idle reflection 真的不冒泡（silent turn 被前端正确丢弃）。

**本期定位是桌面端的 Tier 0 封口**——做完之后 014 的 push 通道在桌面端有了承接位，bedtime / idle reflection 这类主动轮能真正"出现"。**不是产品级"主动陪伴"功能本身**——产品层"什么时候主动开口 / 主动说什么 / 桌宠形象配合"是 Tier 1/2 优化期的事。

### 1.3 与 014 的关系

014 是后端供给侧（main loop + push 协议出口），015 是桌面端消费侧。**接口契约以 014 落定的 push 协议为准**，本期不重新定义协议字段、不动 bridge server / agent runtime。如果消费过程中发现 014 协议层有缺漏，**先与 014 对齐再走**，不擅自改。

### 1.4 上下文同步问题已由 014 解决

主动轮事件在后端落进**用户当前 session 的同一段 `session.events` 流**（见 014 `BedtimeSource` 构造期 `session_id` 绑定 + `AgentRuntime` dispatch 通过同一 `conversation_factory(session_id)` 取 Conversation 的实现）。下次 user 在 chat 窗发话时，agent inner loop 从 conversation history 取上下文能看到主动轮的 assistant_message——**"我说过的话 agent 不记得"这种情况不会发生**。

因此本期前端 UI 层"主动轮和 user 触发轮分流"只是**前端展示侧分离**，后端 `session.events` 仍然共享，上下文不分裂。

### 1.5 与 explorations 的关系

本期承接 [`desktop-completeness`](../../explorations/desktop-completeness/) §4 的明确诉求；调研对象与桌宠交互模式参考 [`desktop-pet-form-factor`](../../explorations/desktop-pet-form-factor/) §1 / §2，但**不锁定具体形态**（spritesheet 协议、9 状态映射等）——本期气泡组件保持占位贴图 + 简单动效即可，Live2D / 状态机贴图等留 Tier 1/3。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **Owner 抽象** | 新增一层 owner 抽象，chat / pet 两个 webview 共享同一份 conversation 事件流；具体 owner 形态（chat 窗作 owner / Rust 侧作 owner / hidden background webview）由 `design.md` 决定 |
| **Owner 双订阅** | Owner 同时订阅 014 的 (a) user 触发流（pull 模式既有 `/ag-ui/run`）和 (b) agent 主动轮流（push channel），按事件来源走不同分发路径 |
| **事件 → 出口 policy** | 不锁死映射规则；做成一层可替换的 policy 模块。出口共两类：`chat-window`（MessageList）和 `pet-bubble`（桌宠气泡）。第一版默认由 `design.md` 决定 |
| **Pet 窗 speech bubble** | 新增气泡组件作为主动轮主出口；位置、动画、超长截断、消失策略、多消息排队、点击穿透处理由 `design.md` 决定 |
| **前端 UI 主动轮分流** | chat 窗 MessageList **不**渲染主动轮事件；主动轮主出口是 pet 气泡；user 触发轮在 chat 窗渲染（pet 气泡是否跟随由 `design.md` 决定） |
| **sessionProjection 兼容** | `frontend/src/stores/sessionProjection.ts` 识别主动轮 `system_trigger` 事件并跳过，不投影、不报错；事件本身在 JSONL 里保留 |
| **Silent turn 前端丢弃** | IdleReflectionSource 类的 silent turn 在前端 owner 层直接丢弃，不进任何 UI 出口（也不进 sessionProjection） |
| **跨 Space / 全屏浮动** | `frontend/src-tauri/tauri.conf.json` 配置 pet 窗 `visible_on_all_workspaces` + 高浮动 window level，让 pet 跟随用户跨虚拟桌面、悬浮在全屏 app 之上 |
| **Dev CLI 端到端** | 复用 014 的 dev CLI 触发链，在前端 dev 流程下端到端验证：BedtimeSource 触发 → 桌宠头上冒气泡；IdleReflectionSource 触发 → 桌宠无反应 + memory 有记录 |

---

## 3. 非目标（Out of Scope）

以下本期**明确不做**，避免范围蔓延：

- **形象状态机**（idle / thinking / speaking / waiting / error / asleep 切占位贴图）：与 owner / 气泡正交，独立可做，留下个需求。气泡冒出来时桌宠表情切 speaking 是自然耦合，但状态机本身够独立。
- **bridge 连接连续性**（重连 + 心跳 + offline 反馈）：稳定性层补完，与本期机制正交，留下个需求。
- **桌宠位置持久化 + 屏幕边界 clamp**：与本期机制正交，留下个需求。
- **专注模式静默判定 / 主动 nudge 桌面端承接**：决策权应在 engine 侧（持有 persona / memory / 上下文），前端只做"展示侧"（收到啥就按 policy 显示啥）。本地 if-then 业务判定本期不做。
- **chat 窗呈现历史主动轮**：本期 sessionProjection 不投影主动轮事件；事件已在 JSONL，未来加投影规则即可，不影响数据完整性。
- **多 session 主动轮路由策略**：沿用 014 v1 "单 session 假设"；多 session 主动轮该往哪个 session 发是后端调度问题，本期前端按事件流消费即可。
- **气泡的产品层细节决策**：什么时机主动开口、桌宠说什么、消息流式 vs 整段出、消失策略对哪些 persona 适配等"主动陪伴产品体验"判断本期不做，由 `design.md` 锁第一版机制默认即可，产品层迭代留 Tier 1/2。
- **bridge 协议 / 014 后端任何改动**：消费侧 only，发现 014 协议有缺漏先与 014 对齐再走。
- **Live2D / 桌宠 spritesheet 协议**：本期气泡走简单占位贴图 + 简单动效，调研里 OpenPet / petdex 等生态接入留 Tier 3。
- **右键菜单 + 双击反应 / 托盘扩展**：调研里桌宠标配，但内容（菜单项里放什么）多数依赖其他模块（persona 切换、勿扰），留下个需求。
- **跨平台窗口能力差异**（Windows / Linux）：本期跨 Space / 全屏浮动只 spike macOS，三平台兼容留下个需求。
- **Voice / IM 通道桌面端接入**：属于通道层，不是桌面端本身的缺口。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；具体接口形态、owner 形态选型、气泡 UI 细节等由 [`design.md`](./design.md) 决定。

### 4.1 前端 Conversation 事件 Owner

- **R-4.1.1 Owner 抽象**：新增一层 owner，作为 conversation 事件流在 chat / pet 两个 webview 之间的共享入口；chat 窗的 `useConversationStore` 不再直接消费 `runAgentStream` / fetch-SSE，改为从 owner 订阅事件。
- **R-4.1.2 不重写既有数据通路**：现有 `frontend/src/services/stream.ts` (`runAgentStream`) / `frontend/src/stores/conversationReducer.ts` (`applyAguiEvent`) / `frontend/src/stores/conversation.ts` (`useConversationStore`) 保留；改造点是"谁调用它们"——从直接被 chat 窗调用改为被 owner 调用，再由 owner 分发给两个 webview。
- **R-4.1.3 Owner 形态由 design 决定**：[`desktop-completeness`](../../explorations/desktop-completeness/) §4.3 (a) 列了三种候选（chat 窗作 owner / Rust 侧作 owner / hidden background webview），各有取舍；具体选哪种由 `design.md` 锁定，requirement 不锁。
- **R-4.1.4 单 source of truth**：同一条 conversation 事件在所有出口（chat / pet）的状态一致——不会出现"chat 窗看到该事件已完成、pet 窗还在显示流式中"这种不一致。

### 4.2 Owner 双订阅（user 触发流 + agent 主动轮流）

- **R-4.2.1 双订阅**：Owner 同时订阅 (a) 014 既有 pull 模式（user 触发的 `/ag-ui/run` SSE）和 (b) 014 新增 push channel（agent 主动轮 / 镜像 user 触发轮）。
- **R-4.2.2 事件来源标识**：每条到达 owner 的 conversation 事件携带"触发源"信息（user-triggered 还是 agent-active），让下游 policy 据此分发。
- **R-4.2.3 不重复消费**：014 push channel 同时镜像 user 触发流和主动轮（见 014 M14.6 "Pull 路径镜像复制 → push subscriber"）。owner 要保证 pull + push 两条订阅不导致同一事件被消费两次（去重或选其一）；具体策略由 `design.md` 决定。

### 4.3 事件 → 出口分发 Policy

- **R-4.3.1 出口枚举**：本期 owner 支持的 UI 出口共两类——`chat-window`（chat 窗 MessageList）、`pet-bubble`（pet 窗 speech bubble）。
- **R-4.3.2 Policy 可替换**：owner 不锁死"哪类事件去哪个出口"的具体规则；做成一层可替换的 policy 模块，第一版默认实现由 `design.md` 决定（默认实现要满足本期 §4.4 的分流要求）。
- **R-4.3.3 0/1/2 出口都允许**：一条事件理论上可去 0（silent turn）、1（user 触发轮默认仅 chat 窗）、2（policy 决定时）个出口。
- **R-4.3.4 易变维度留扩展点**：policy 是后期产品迭代最易变的维度（呼应 [`coding-design`](../../../.cursor/rules/coding-design.mdc) "识别最易变维度，给易变策略留扩展点"）；本期不锁死规则就是为了让产品层"主动轮要不要镜像到 chat 窗 / 加新出口（系统通知等）/ 勿扰直接丢弃"等演进只改 policy，不动 owner 框架。

### 4.4 前端 UI 层主动轮分流

- **R-4.4.1 主动轮主出口是 pet 气泡**：来自 014 主动轮流的"用户可见"事件（assistant_message / TextDelta 等），第一版 policy 下**只**送到 `pet-bubble` 出口。
- **R-4.4.2 chat 窗不渲染主动轮**：第一版 policy 下，chat 窗 MessageList **不**渲染来自主动轮的任何事件。
- **R-4.4.3 user 触发轮默认走 chat 窗**：user 在 chat 窗发起的轮次，第一版 policy 下 chat 窗正常渲染；pet 气泡是否跟随由 `design.md` 决定。
- **R-4.4.4 Silent turn 前端丢弃**：来自 IdleReflectionSource 类的 silent turn 事件（按 014 协议标识为 silent / 仅 memory），在 owner 层就被丢弃，不进任何出口、不进 sessionProjection。

### 4.5 sessionProjection 兼容

- **R-4.5.1 主动轮事件跳过投影**：`frontend/src/stores/sessionProjection.ts` 识别 014 落进 `session.events` 的 `system_trigger` 事件（或等价标识），**跳过**投影、不报错——不进 chat 窗历史回看。
- **R-4.5.2 不删源数据**：主动轮事件在 session JSONL 里保留（已由 014 R-4.4.3 落定），sessionProjection 只是不消费而非丢弃；未来想做"chat 窗也呈现历史主动轮"只需加投影规则。
- **R-4.5.3 现有投影行为零退化**：user 触发轮事件的投影（含 ToolCallRequest / ToolCallResult / TurnDone / FRIENDLY_FALLBACK 兜底等）跟现状完全一致。

### 4.6 Pet 窗 Speech Bubble

- **R-4.6.1 气泡组件**：新增 pet-bubble 组件挂在 pet 窗，订阅 owner 分发到本出口的事件并渲染。位置（贴形象上方 / 下方 / 屏幕边缘翻转）、动画（冒出 / 停留 / 消失）、超长截断与展开、消失策略（停留时长 / 用户读完判定）、多消息排队、点击穿透与 `[data-hit]` 处理等由 `design.md` 决定。
- **R-4.6.2 主动轮 vs 流式渲染**：气泡能承接两种渲染模式——主动轮（整段冒出）vs user 触发轮（如果第一版 policy 决定跟随）流式逐字增量。
- **R-4.6.3 与 pet 窗形象不冲突**：气泡的点击区与 pet 窗 `usePetPassthrough` 透明区穿透机制不冲突；具体合一规则（哪些 DOM 标 `[data-hit]`、气泡是否阻挡形象拖拽）由 `design.md` 决定。

### 4.7 跨 Space / 全屏浮动

- **R-4.7.1 跨虚拟桌面**：pet 窗在 macOS 上跟随用户跨 Space（`visible_on_all_workspaces` 或等价 Tauri 配置）。
- **R-4.7.2 悬浮全屏 app 之上**：pet 窗 window level 高于全屏 app 层级，确保主动轮气泡冒出来时不被全屏 app 遮挡。
- **R-4.7.3 macOS 优先**：本期跨平台只 spike macOS；Windows / Linux 行为差异留下个需求。

### 4.8 Dev CLI 端到端

- **R-4.8.1 Bedtime 端到端**：复用 014 dev CLI 的 BedtimeSource "立刻触发" 触发链，前端 dev 流程下能观察到——bedtime 触发 → 桌宠头上冒气泡（pet-bubble 出口）→ chat 窗 MessageList **不出现**该消息 → session JSONL 里能找到本轮事件。
- **R-4.8.2 IdleReflection 端到端**：复用 014 dev CLI 的 IdleReflectionSource 触发链，前端 dev 流程下能观察到——idle 触发 → 桌宠**无任何反应**（气泡不冒、表情不变）→ memory 里有记录（按 014 AC-6 标准）。
- **R-4.8.3 User 触发轮回归**：user 在 chat 窗输入触发的对话 → chat 窗 MessageList 正常渲染（行为零退化）；pet 气泡是否同步显示按第一版 policy。
- **R-4.8.4 入口跨平台**：如本期新增 dev 启动入口，按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 在 `scripts/{name}/` 提供 `run.sh` + `run.ps1` 双端 wrapper 并登记 `scripts/README.md`。

### 4.9 向后兼容

- **R-4.9.1 既有 chat 窗对话流零变更**：user 在 chat 窗发起对话的端到端行为（输入 → SSE → MessageList 渲染 → 流式逐字 → 工具卡片 → TurnDone）完全不变。
- **R-4.9.2 既有 pet 窗形象与拖拽零变更**：pet 窗左键拖拽、`usePetPassthrough` 透明区穿透判定、托盘三项菜单行为完全不变。
- **R-4.9.3 sessionProjection 既有投影行为零退化**：现有 user 触发轮事件投影逻辑（含 FRIENDLY_FALLBACK / RUN_ERROR 兜底）完全不变。
- **R-4.9.4 bridge / engine 既有接口零改动**：本期 only 在前端消费侧，不动 014 协议字段、bridge server、agent runtime。

---

## 5. 使用约束

- **以 014 协议为准**：消费 014 push channel 协议时，发现需要动协议字段或后端行为，**先与 014 对齐再走**，不擅自改。
- **真实 LLM 调用授权**：dev CLI 端到端验证会经由 014 的 BedtimeSource / IdleReflectionSource 触发 LLM 调用，跑前需获用户授权；本期前端**不引入额外维度的 LLM 调用**。
- **跨平台开发脚本约定**：本期如新增"非写代码"开发操作（含 dev 启动入口、调试脚本），按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落地（`run.sh` + `run.ps1`），并在 `scripts/README.md` 登记。
- **frontend-ui-conventions 约束**：本期新增 UI 组件（气泡、owner、policy 配置等）遵循 [`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc)。

---

## 6. 验收标准

> 本节 AC 全部是**机制层面的过程性标准**——验证"owner / 气泡 / push 订阅 / 跨窗口分发能跑通、既有路径不退化"，**不含任何"气泡好不好看 / 主动开口时机对不对 / 用户体验如何"的产品层判断**。

- **AC-1 Owner 抽象可订阅可分发**：chat 窗的 `useConversationStore` 从 owner 取事件、pet 窗的气泡 store 从同一个 owner 取事件；两个 webview 看到的同一条事件状态一致；现有 chat 窗对话端到端行为零退化（user 输入 → 流式输出 → 工具卡片 → TurnDone 全流程不变）。
- **AC-2 双订阅去重生效**：owner 同时订阅 pull 模式和 push channel，同一条 user 触发轮事件不被两个出口重复渲染（具体去重策略由 `design.md` 锁，AC 只验"不重复"）。
- **AC-3 事件 → 出口 policy 可替换**：测试中用替换 policy 的方式（mock 一个把所有事件都路由到 `pet-bubble` 的 policy）能让原本走 chat 窗的事件也出现在气泡，证明 policy 是真的可替换的扩展点而非硬编码。
- **AC-4 主动轮分流端到端**：触发 BedtimeSource 主动轮 → pet 窗 speech bubble 渲染一条 assistant_message → chat 窗 MessageList **完全没有该消息**；该轮事件在 session.events JSONL 里能找到。
- **AC-5 Silent turn 前端丢弃**：触发 IdleReflectionSource → pet 气泡**不冒**、chat 窗 MessageList **不出现**、sessionProjection 跳过；后端 `memory.observe` 按 014 AC-6 正常被调用、fragment 形状不变。
- **AC-6 sessionProjection 兼容**：在含主动轮 `system_trigger` 事件的 session JSONL 上跑 sessionProjection，不报错；user 触发轮事件投影出来的 ChatMessage 序列与不含主动轮事件时完全一致（断言相等）。
- **AC-7 跨 Space / 全屏浮动**：macOS 上，pet 窗切换 Space 后仍可见；启动一个全屏 app（如全屏视频播放器），pet 窗仍悬浮在其上。
- **AC-8 既有路径零退化**：`./scripts/check`（lint + typecheck + test）全绿；user 在 chat 窗输入的对话全流程行为不变；pet 窗左键拖拽 / 透明区穿透 / 托盘菜单行为不变。
- **AC-9 Dev CLI 端到端跑通**：按 014 dev CLI 触发链 + 本期前端运行，能在桌面上观察到 R-4.8.1 / R-4.8.2 / R-4.8.3 描述的三种现象；过程已记录到 `progress.md`。

---

## 7. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-13
- **确认时间**：2026-06-13
- **承接**：[`docs/explorations/desktop-completeness/`](../../explorations/desktop-completeness/) §4 + [需求 014](../014-engine-main-loop-and-bridge-push/) §3 "桌面端 Tier 0"
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
