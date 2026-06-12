# 014 · 引擎主循环与桥推送通道（agent main loop + bridge push channel）

> Engine Main Loop & Bridge Push Channel
>
> 把 agent 编排层的调度模型从 inner loop（被动响应 user 输入的 ReAct 循环）升级为 outer / main loop（事件驱动、可主动发起），并打通 `agent-bridge` agent→桌面端的主动 push 通道，让"非 user 触发"的轮次有承接位。本期是基础设施层补完，不是"主动陪伴"产品功能本身。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

当前 agent 编排层（[`agent/`](../../../agent/) 包）已具备 ReAct 形态的多轮 tool-calling 循环，按业界"ReAct + tool-using = agent"门槛已过关——见 [`docs/explorations/engine-completeness/`](../../explorations/engine-completeness/) §2。但若要从"多轮对话引擎"升级为能被称作 **agent runtime** 的形态，主要缺口集中在两处：

- **没有 outer loop**：`agent/src/agent/conversation.py:305` 的 inner loop 一旦被调起就一定要生成回复，无法接受"非 user 事件"作为驱动源。`_observe_turn`（`conversation.py:672`）实际上已经是一个事实上的 PostTurn hook，但被硬编码在 inner loop 末尾。
- **bridge 是 pull 模式**：[`agent-bridge`](../../../agent_bridge/) 当前只支持客户端 POST `/ag-ui/run` 触发流，没有"agent 主动向桌面推消息"的承接通道。

详细推导见 [`engine-completeness`](../../explorations/engine-completeness/) §1–§4 与 [`desktop-completeness`](../../explorations/desktop-completeness/) §4；本需求**不复述**。

### 1.2 这次要做什么

按 [`engine-completeness`](../../explorations/engine-completeness/) Tier 0 + [`desktop-completeness`](../../explorations/desktop-completeness/) §4.3 (c)（"协议层必须同期定"）落地：

- **engine 侧**：立 `AgentRuntime` 调度内核 + `EventSource` 抽象 + inbox 事件队列 + `dispatch_system_turn` 入口；落 Hook 四点位（PreTurn / PostTurn / PreToolUse / PostToolUse）；把 `_observe_turn` 从硬编码迁到 PostTurn hook 注册。
- **EventSource 实例**：`UserSource`（包装现有 send / stream，向后兼容）、`BedtimeSource`（A 类：定时主动发声示例）、`IdleReflectionSource`（D 类：空闲触发 silent turn 落 memory 示例）。
- **bridge 侧**：定稿 agent→桌面 push 通道协议，落 server 端实现，配 dev CLI 验证客户端。

**本期定位是基础设施层的能力补完**——main loop 跑通后下游（产品级"主动陪伴"功能、Tier 2 后台 tool / Permission / Sub-agent）才有承接位。bedtime / idle reflection 这两个示例 source 是用来端到端验证 main loop 通了 + 给未来接 source 留范例，**不是产品交付**。

### 1.3 与并发需求 013 memory pass-1 的协同

并发需求 **013 memory pass-1**（处理 [`issue 003`](../../issues/003-memory-eval-baseline-2026-06-12/) baseline 弱点）同期进行，仅改 `memory/` 模块内部（extraction / retrieval / store）。两边硬约束（来自 013 提案，本期 §5 落实）：

- `Memory.observe(fragment)` / `Memory.retrieve(query, ...)` 签名不变
- `ConversationFragment` 喂给 `observe` 的数据形状不变（保留 user 原话 / role 等不被压缩 / 类型化）

`_observe_turn` 本期迁到 PostTurn hook 注册——迁移**只换"被谁调用"，不改"喂什么"**；本期 AC 专项验证 `memory.observe` 行为零退化。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| `AgentRuntime` 调度内核 | 单消费者串行 dispatch（保证一次只跑一轮），inbox 事件队列，复用现有 inner loop 不重写 |
| `EventSource` 抽象 + inbox 协议 | 各 source 异步往 inbox 塞 `AgentEvent`，dispatch 端按事件类型 translate 成 conversation 输入 |
| `UserSource` | 包装现有 `Conversation.send` / `stream` 入口，让 `agent-cli` / `agent-bridge` 既有 user 触发路径透明走 main loop，向后兼容 |
| `BedtimeSource` | A 类（定时主动发声）示例：到指定时间塞一条 `ScheduledTick(kind="bedtime")` system-turn，按 persona 自然说一句 |
| `IdleReflectionSource` | D 类（silent turn 落 memory）示例：系统空闲 N 分钟塞一条 `IdleTick`，输出仅入 memory 不冒泡到用户 |
| `dispatch_system_turn` 入口 | `Conversation` 新增"系统级触发轮"入口，参数区分 output 去向（用户可见 / 仅 memory） |
| Hook 四点位 | PreTurn / PostTurn / PreToolUse / PostToolUse，注册顺序 = 执行顺序，单 hook 异常隔离不污染主流程 |
| Pre-\* 短路语义 | PreTurn hook 返回特定值能让 main loop 跳过本轮（用于"专注模式静默"等）；PreToolUse hook 返回特定值能阻止 tool 执行（为 Tier 2 Permission 留口子） |
| `_observe_turn` 迁移 | 从 `conversation.py` 内硬编码改为 PostTurn hook 注册；喂给 `memory.observe` 的 fragment 形状完全不变 |
| Bridge agent→桌面 push 通道协议 | 客户端可订阅的常驻通道，协议层区分"user 触发流"与"agent 主动轮"；具体形态（长 SSE / WebSocket）在 `design.md` 锁 |
| Bridge push server 实现 | 接 `AgentRuntime` 主动轮事件，按协议向已订阅客户端推送 |
| Dev CLI 验证客户端 | 一条命令能订阅通道、看到 bedtime / idle reflection 触发的主动轮事件，作为本期端到端验证手段；Python 实现在 `agent_bridge/dev/`，启动入口按项目跨平台规范在 `scripts/{name}/` 双端落地（`run.sh` + `run.ps1`） |

---

## 3. 非目标（Out of Scope）

以下本期**明确不做**，避免范围蔓延（多为 explorations 中归到下游 Tier 或下个需求的项）：

- **桌面端 Tier 0**：[`desktop-completeness`](../../explorations/desktop-completeness/) §4 的"桌宠气泡 + 跨窗口订阅 + 前端 owner"——本期协议出口已定，前端消费侧留下个需求。
- **engine Tier 1**：reasoning / thinking 通道（新事件类型）、并行 tool 调用、ToolRegistry 运行时化 + MCP 接入——独立可做、与 main loop 不耦合，留下个需求。
- **engine Tier 2 / Tier 3**：后台 / 异步 tool（依赖本期）、Permission / 工具审批（依赖 Hook）、Sub-agent 协作（依赖本期）、Plan / Confirm 状态机——本期 Hook 四点位先立空骨架供其将来挂。
- **Cron 表达式 DSL**：CronSource 内部按"到指定时间触发一次"的最小调度即可，不做 cron 语法 / human-friendly DSL；具体调度形态（asyncio sleep / scheduler）由 `design.md` 决定。
- **persona schema 改动**：bedtime 时间 / idle 阈值这类配置本期硬编码合理 default + 构造期注入，**不动 persona 数据结构**——未来要做"用户偏好可配"再立项。
- **`Memory.observe` / `retrieve` 签名改动 + `ConversationFragment` 形状改动**：与 013 memory pass-1 协同硬约束，详见 §5。
- **任何 agent / system_prompt / context / persona 模块的对外签名改动**：本期改造点集中在 conversation 外部（增加 `AgentRuntime`）+ conversation 内部对外新增入口（`dispatch_system_turn`），不改既有 `send` / `stream` 接口形态。
- **跨 session / 多 agent 协作**（[`engine-completeness`](../../explorations/engine-completeness/) E 类事件源）—— Sub-agent 在 Tier 2，本期不做。
- **环境感知信号采集**（C 类）：依赖桌面端采集侧，本期不做。
- **bridge OpenAI / AG-UI 既有 pull 出口的任何改动**：本期只新增 push 通道，pull 路径行为零退化。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；具体接口形态、注册 API、协议字段等由 [`design.md`](./design.md) 决定。

### 4.1 调度内核（AgentRuntime + inbox）

- **R-4.1.1 单消费者 dispatch**：`AgentRuntime` 持有一个 inbox（事件队列），单消费者串行 dispatch，保证同一时刻只跑一轮、避免并发改 session。
- **R-4.1.2 不重写 inner loop**：`Conversation` 现有 inner loop（`conversation.py:305`）作为子例程被 `AgentRuntime` 调用，**不改动**；`Conversation.send` / `stream` 既有入口保留供 `UserSource` 包装。
- **R-4.1.3 90% 静默兜底**：`AgentRuntime` 在收到事件后允许 PreTurn hook（见 R-4.3.3）返回"不开口"决定，跳过本轮 dispatch；这是 main loop 区别于 inner loop 的关键能力（inner loop 一旦被调起就一定生成回复）。

### 4.2 EventSource 抽象 + 示例 source

- **R-4.2.1 EventSource 协议**：定义统一的 `EventSource` 协议，能异步往 `AgentRuntime` inbox 塞 `AgentEvent`；具体协议字段（事件类型、payload、来源标识）由 `design.md` 决定。
- **R-4.2.2 UserSource 向后兼容**：`UserSource` 包装现有 `Conversation.send` / `stream` 调用，让 `agent-cli` / `agent-bridge` 既有 user 触发路径透明走 main loop，**外部观测行为零退化**（事件流、session 落盘格式、tool 调用展示等不变）。
- **R-4.2.3 BedtimeSource（A 类示例）**：构造时可注入 bedtime 时间（默认 sane value）；到点向 inbox 塞 `ScheduledTick(kind="bedtime")` 事件；dispatch 时按 persona 自然说一句"很晚了，该睡了"语义的话，事件流走正常 assistant_message 出口。
- **R-4.2.4 IdleReflectionSource（D 类示例）**：构造时可注入 idle 阈值（默认 sane value）；系统空闲达到阈值时塞 `IdleTick` 事件；dispatch 时跑一次 silent turn——**输出仅入 memory（通过 PostTurn hook 走 `memory.observe`）、不向用户输出 `assistant_message`**。
- **R-4.2.5 配置不进 persona schema**：bedtime 时间、idle 阈值等配置本期通过 source 构造期注入，**不动 persona 数据结构**——未来要做"用户偏好可配"再立项。

### 4.3 Hook 体系

- **R-4.3.1 四点位**：`AgentRuntime` 在 dispatch 链上落四个 hook 点位——PreTurn（事件入 dispatch 之前）、PostTurn（一轮 dispatch 完成之后）、PreToolUse（tool 执行前）、PostToolUse（tool 执行后）。
- **R-4.3.2 注册与执行**：每个点位可注册多个 callback；注册顺序 = 执行顺序；单个 hook 抛异常**不影响其他 hook 与 dispatch 主流程**（错误隔离 + 日志记录）。
- **R-4.3.3 Pre-\* 短路**：PreTurn hook 返回特定语义值能让 main loop 跳过本轮 dispatch（不进 inner loop、不出 `assistant_message`）；PreToolUse hook 返回特定语义值能阻止 tool 执行（直接 fail 回 LLM）。具体返回值约定由 `design.md` 决定。
- **R-4.3.4 Post-\* 不短路**：PostTurn / PostToolUse hook 不支持短路（结果已产生），仅作旁路观察 / 副作用。

### 4.4 dispatch_system_turn 入口

- **R-4.4.1 系统级触发轮入口**：`Conversation` 新增 `dispatch_system_turn` 类入口（具体名 / 签名由 `design.md` 决定），让 main loop 能把"非 user 事件"翻译成 conversation 能消费的输入而无需伪装成 `user_message`。
- **R-4.4.2 Output 去向区分**：入口参数支持区分 output 去向——"用户可见"（`assistant_message` 走正常 stream 出口）vs "仅 memory"（silent turn，输出只走 `memory.observe`，不出 `assistant_message`、不冒泡到 bridge stream）。
- **R-4.4.3 事件流可回放**：无论 output 去向是"用户可见"还是"仅 memory"，本轮在 session JSONL 都要落事件，让历史可回放、下次召回可用——与 [`engine-completeness`](../../explorations/engine-completeness/) §4.2 A "无论哪种都要在 session 里落一条系统触发轮事件"一致。

### 4.5 _observe_turn 迁移到 PostTurn hook

- **R-4.5.1 去硬编码**：`_observe_turn`（`conversation.py:672`）从 inner loop 末尾的硬编码调用，改为通过 `AgentRuntime` 的 PostTurn hook 注册。
- **R-4.5.2 行为完全不变**：迁移**只换"被谁调用"，不改"喂什么"**——`memory.observe` 收到的 `ConversationFragment` 形状（user 原话 / role / 时序等字段）必须与迁移前完全一致；调用时机（每轮 dispatch 完成之后）保持。
- **R-4.5.3 silent turn 走同一通道**：`IdleReflectionSource` 触发的 silent turn 也通过同一个 PostTurn hook 把结果喂给 `memory.observe`，复用迁移后的统一通道。

### 4.6 Bridge agent→桌面 push 通道

- **R-4.6.1 常驻订阅通道**：`agent-bridge` 新增一个客户端可订阅的常驻通道（具体形态长 SSE / WebSocket 由 `design.md` 决定），与现有 pull 模式 `/ag-ui/run` **并存且互不影响**。
- **R-4.6.2 协议区分两类流**：协议层显式区分"user 触发流"（pull 模式既有形态）与"agent 主动轮"（main loop 系统触发轮），让客户端能据此决定如何呈现（专注模式可能只显示 user 触发流、丢弃主动轮）。
- **R-4.6.3 Server 端接 AgentRuntime**：bridge server 订阅 `AgentRuntime` 出来的主动轮事件，按 R-4.6.2 协议格式向已订阅客户端推送。
- **R-4.6.4 Pull 路径不退化**：现有 OpenAI / AG-UI pull 出口、`agent-cli --bridge` 模式行为完全不变。

### 4.7 Dev CLI 验证客户端

- **R-4.7.1 端到端订阅 + 触发**：在 `agent_bridge/dev/` 下提供一个 dev CLI，能 (a) 订阅 R-4.6 push 通道、(b) 在另一进程触发 `BedtimeSource` / `IdleReflectionSource` demo（如把 bedtime 注入"立刻触发"）、(c) 看到主动轮事件按协议正确到达。
- **R-4.7.2 不入产品依赖**：CLI 工具不进 `agent-bridge` 运行时打包产物（具体 packaging 处理由 `design.md` 决定），明确"开发期工具"语义。
- **R-4.7.3 启动入口跨平台双端**：Python 实现挂在 `agent_bridge/dev/` 下，启动入口按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 在 `scripts/{name}/` 提供 `run.sh` + `run.ps1` 双端 wrapper，并在 `scripts/README.md` 登记。开发者直接跑 wrapper、不需记 `uv run python -m ...` 命令。

### 4.8 向后兼容

- **R-4.8.1 既有外部接口零变更**：`Conversation.send` / `stream`、`agent-bridge` OpenAI / AG-UI pull 出口、`agent-cli` 行为完全不变。
- **R-4.8.2 既有事件流格式零变更**：`UserSource` 包装下，session JSONL 落盘格式、stream 事件类型与字段不变。

---

## 5. 使用约束

- **与 013 memory pass-1 协同硬约束**（来自 013 提案）：
  - `Memory.observe(fragment)` / `Memory.retrieve(query, ...)` 签名不变
  - `ConversationFragment` 喂给 `observe` 的数据形状不变（保留 user 原话 / role 等不被压缩 / 类型化）

  本期发现需要动这两个接口或 fragment 形状时，**先与 013 对齐再走**，不擅自改。
- **真实 LLM 调用授权**：`BedtimeSource` / `IdleReflectionSource` demo 触发会调用 LLM（前者出 `assistant_message`、后者出 `memory.observe` 链路里的抽取调用），跑前需获用户授权；本期不引入额外维度的 LLM 调用模式。
- **跨平台开发脚本约定**：所有新加的"非写代码"开发操作（含 dev CLI 启动入口）按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落地（`run.sh` + `run.ps1`），并在 `scripts/README.md` 登记。

---

## 6. 验收标准

> 本节 AC 全部是**机制层面的过程性标准**——验证"main loop / Hook / push 通道能跑通、既有路径不退化"，**不含任何"主动开口好不好听 / 时机对不对"的产品体验判断**。

- **AC-1 main loop dispatch 可跑通**：`AgentRuntime` 启动后能从 inbox 消费一条 user / system 事件、跑完一次 inner loop dispatch、事件正确落 session JSONL；`UserSource` 包装下 `agent-cli` 既有 user 路径行为零退化。
- **AC-2 Hook 四点位可注册可触发**：每个点位有自检测试覆盖"注册 → dispatch 触发 → callback 被调用"的最小路径；单 hook 抛异常不打断主流程（其他 hook 仍调用、dispatch 仍完成）。
- **AC-3 Pre-\* 短路语义可用**：PreTurn 短路能让 main loop 跳过本轮（不进 inner loop、不出 `assistant_message`）；PreToolUse 短路能阻止 tool 执行（tool 不被调用、fail 直接回 LLM）。
- **AC-4 `_observe_turn` 迁移行为零退化**：迁移前后跑同一组测试 conversation，对 `memory.observe` 收到的 `ConversationFragment` 做两层断言——(a) 关键字段（user 原话、role、时序、tool 调用相关字段；具体字段清单由 `design.md` 落实）逐项相等；(b) 整体序列化 hash 一致兜底。现有 conversation 集成测试全绿。
- **AC-5 BedtimeSource demo 端到端**：在测试环境注入"立刻触发"的 `BedtimeSource`，能跑完一次 dispatch、出一条 `assistant_message`、事件正确落 session 且与 user 触发流可区分。
- **AC-6 IdleReflectionSource silent turn 端到端**：模拟空闲达阈值，`IdleReflectionSource` 触发一次 silent turn，**输出只入 memory（`memory.observe` 被调用且 fragment 形状符合 R-4.5.2 约束），不出 `assistant_message`、不冒泡到 bridge stream**；session 中能回放出本轮系统触发事件。
- **AC-7 Bridge push 通道端到端**：dev CLI 订阅 push 通道，触发 `BedtimeSource` / `IdleReflectionSource` demo 后能按 R-4.6.2 协议格式收到主动轮事件，并能与 user 触发流区分；现有 OpenAI / AG-UI pull 出口跑通既有 e2e 测试不退化。
- **AC-8 Dev CLI 落地**：dev CLI Python 实现在 `agent_bridge/dev/` 下、不进运行时打包；启动入口在 `scripts/{name}/` 双端（`run.sh` + `run.ps1`）并已在 `scripts/README.md` 登记；`./scripts/check` 全绿。

---

## 7. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-12
- **确认时间**：2026-06-12
- **承接**：[`docs/explorations/engine-completeness/`](../../explorations/engine-completeness/) §4 + [`docs/explorations/desktop-completeness/`](../../explorations/desktop-completeness/) §4.3 (c)
- **协同需求**：013 memory pass-1（并发进行，硬接口契约见 §5）
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
