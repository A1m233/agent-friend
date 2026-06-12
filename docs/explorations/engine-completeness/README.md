# 引擎层补全（Engine Completeness）

> 本文是过程笔记（exploration），定位于"站在当前 agent 编排层（`agent/` 包）的视角，盘点还差什么、怎么排"。**不锁定优先级、排期或具体接口**——真要做某一项时再去 `docs/decisions/` 或 `docs/requirements/` 立项。

## 1. 背景

参照对象：Claude Code 一类"产品级 agent"。当前 agent-friend 编排层的核心循环是 ReAct 形态的 tool-calling loop（`agent/src/agent/conversation.py:305`），具备多轮对话、工具调用、persona / model / channel 切换、上下文管理、memory 旁路等基础能力——按业界"ReAct + tool-using = agent"的门槛已经过关。

但若要从"多轮对话引擎"升级到能被称为 **agent runtime** 的形态，仍有若干缺口。本文把它们罗列、按重要性与依赖关系排序，并单独展开最大的一块——**agent main loop**。

## 2. 现状：编排层已经做到位的骨架

- **Inner loop**：单轮 user 输入内的 ReAct tool-calling 循环，触上限有兜底（`conversation.py:305`、`_finalize_on_tool_loop_limit` 在 `conversation.py:763`）
- **Event-sourced session**：JSONL append-only，从事件流派生 state，中断 `partial=True` 落盘
- **槽位化 system_prompt**：`SystemPromptComposer` 5 个默认槽位有序装配（`system_prompt/composer.py`）
- **Context 管理**：`ContextManager` 协议 + budget snapshot + compaction record，3 种策略（naive / fifo / summarizing）
- **Memory 旁路**：`observe` / `retrieve` 接口；召回结果注入 `extra_context`
- **可观测**：`last_memory_context` / `last_context_notes`
- **协议化暴露**：`agent_bridge` 双协议（OpenAI / AG-UI）HTTP + SSE

## 3. 缺失模块清单

按 Tier 0 → Tier 3 排序。Tier 内不分先后，按依赖关系归类。

### Tier 0 — 基础设施（必须先做，强耦合，**捆绑做**）

**Agent main loop + Hooks 体系**

- 当前 conversation 只有 inner loop，没有"被非 user 事件唤醒"的入口
- Hook 的触发位置本质上是 main loop dispatch 的生命周期点：PreTurn / PostTurn 在 main loop 边界，PreToolUse / PostToolUse 在 inner loop 边界
- 现有 `_observe_turn`（`conversation.py:672`）已经是一个事实上的 PostTurn hook，但硬编码在 inner loop 末尾
- 分开做的代价：第一版 main loop 必然会再硬编码若干钩子位，等 hooks 立项时返工拆出来

→ 详见 §4。

### Tier 1 — 独立、收益直接（可单独做，不依赖 Tier 0）

**流式协议补 reasoning / thinking 通道**
现代 model（Anthropic、DeepSeek-R 系列）会单独发 reasoning stream。当前 `ConversationEvent` 只有 4 类（`TextDelta` / `ToolCallRequest` / `ToolCallResult` / `TurnDone`），reasoning 段会被丢弃。补一种事件类型即可，前端能接到"AI 正在思考……"信号。

**并行 tool 调用**
`conversation.py:351` 是 `for tc in tool_calls:` 串行执行。LLM 一次返回 N 个独立 tool_use 时直接 N 倍延迟。改造为并发 invoke（同步并行，不依赖 main loop）。

**ToolRegistry 运行时化 + MCP 接入**
当前 ToolRegistry 构造期固化（`tools/registry.py`），注释里已留口子但未实现。MCP 接入要求按 session / persona 动态装配工具集。独立模块。

### Tier 2 — 依赖 Tier 0

**后台 / 异步 tool**
`Tool.invoke` 同步返回（`tools/protocol.py:64`）。长任务（爬网页、长 LLM 调用、语音转写）当前只能阻塞 conversation。改造为后台跑、完成后通过 main loop inbox 回包。**必须先有 main loop**。

**Permission / 工具审批**
敏感工具（写文件、调用户机器 API）需要"先问用户再执行"。最自然的实现是注册一个 PreToolUse hook。**必须先有 Hooks 体系**。

**Sub-agent / fan-out 协作**
子 agent 完成后通过 main loop inbox 回包到主 agent。桌宠定位下不紧迫；做"研究型问答 / 长文档整理"时会用到。

### Tier 3 — 方向性，看产品决定

**Plan / Confirm 状态机**
"用户审视计划 → approve → 执行"的明确状态机。需要 session 状态机扩展（不只是 messages，要有 plan / mode 状态）。桌宠多数场景不需要；只有做"帮我整理 / 多步操作"形态时才有价值。

## 4. Agent main loop 详细展开

### 4.1 与 inner loop 的本质区分

当前的 inner loop（`conversation.py:305`）是经典 ReAct 形态：

```
LLM → tool_calls? → 执行 tools → LLM → ... 直到 stop_reason != "tool_use" 或触上限
```

它的驱动源只有一个（LLM 是否说 `tool_use`），生命周期就是一次 `stream()` 调用。

**main loop（outer loop）是 agent 的执行入口本身**，常驻、由任意事件源驱动。对话只是它处理的众多事件类型中的一种。

| | inner loop | main loop |
|---|---|---|
| 触发 | LLM 在 stream 里吐 `tool_use` | 任意事件源 |
| 生命周期 | 一次 `send()` 调用 | agent 在线期间常驻 |
| 输入 | LLM 的 `stop_reason` | user 输入、定时器、后台 tool 完成、文件变更、子 agent 回包、hook event…… |
| 状态 | 局部变量 | 持久化在 session 上 |

状态机示意：

```
[ IDLE ] ──收到事件──▶ [ 构造 turn input ] ──▶ [ 跑 inner loop ] ──▶ 落事件 ──▶ [ IDLE ]
   ▲                                                                              │
   └──────────────────────── 等下一个事件 ◀───────────────────────────────────────┘
```

**关键洞察**：90% 的事件 agent 应该选择"不开口"。inner loop 没有这个概念——它一旦被调起就一定要生成回复。outer loop 才能做"看到事件、判断后保持沉默"。

### 4.2 事件源分类

> **以下 6 类仅作举例**，是当前能想到的、对桌宠定位最具代表性的事件源。**不构成穷举**——实际产品演进中应继续发散更多类型（例如：系统通知接入、IM 消息桥接、健康数据触发、智能家居信号、用户情绪检测等）。

**A. 定时主动发起**

最经典的"非对话触发"。
- 用户设了"每晚 11 点提醒我休息（很晚了，该睡了）" → `ScheduledTick(kind="bedtime")` → 不走 user 消息路径；main loop 注入 system prompt"现在是约定的休息提醒时间，按你当前 persona 自然地说一句"，跑一次 inner loop，输出推到桌宠气泡
- 用户生日 → `ScheduledTick(kind="anniversary")` → 翻 memory 里相关回忆，主动开口
- 日历到点 → `CalendarHit(event="组会")` → 主动开口提醒

无论哪种，都需要在 session 里落一条"系统触发轮"事件，让历史可回放、下次召回能用。

**B. 后台任务回包**

让 tool 摆脱"必须同步阻塞"约束。
- 30 分钟前用户说"帮我看下今晚有什么电影"，agent 启了个 async `web_search` 后回了一句"我查查" → 工具完成后发 `BackgroundTaskDone(task_id, result)` → main loop 判断是否合适打断（用户当前 idle？专注模式？）→ 跑一轮把结果用人话讲出来

当前 `Tool.invoke` 同步返回（`tools/protocol.py:64`），长任务直接卡死 conversation。这条事件源是后台 tool 落地的承接点。

**C. 环境信号 / 外部 webhook**

让 agent 能"感知到对话外的世界"。
- 桌面端检测到用户连续敲键盘 90 分钟没起身 → `EnvSignal(kind="long_focus", minutes=90)` → 按 persona 决定要不要提醒；很可能选择沉默
- 用户切到 VS Code → `EnvSignal(kind="app_switch", to="vscode")` → 多数情况什么都不做；偶尔接一句"开始干活啦？"
- 邮件 / IM 来消息（如果用户允许接入）→ `ExternalNotification(...)` → 视优先级与 persona 决定是否转告

这一类事件源最能体现 main loop 价值：**绝大多数情况 agent 应该判断后保持沉默**。

**D. 自我维护 / 反思（idle work）**

silent turn——输出落到 memory 而不是 assistant_message。
- 系统空闲 10 分钟 → `IdleTick(idle_minutes=10)` → 跑一次 memory consolidation：抽取值得长存的事实、合并重复条目、衰减老记忆
- 每天凌晨 4 点 → `ScheduledTick(kind="nightly_reflection")` → 对当天对话做 self-reflection 落进 memory（人在睡觉、不抢注意力）

`_observe_turn`（`conversation.py:672`）是目前唯一的"silent 旁路"，但被绑死在每轮 turn 末尾、由用户输入驱动。Idle work 需要的是脱离 user 触发的常驻调度。

**E. 跨 session / 多 agent 协作**

- 另一个 persona 的 session 里发生了事（用户对 persona A 说"别告诉 B"）→ `CrossSessionEvent` → main loop 在 persona B 的 session 里塞一条 system 注入
- 子 agent（异步研究任务）完成 → `SubAgentDone(report)` → 主 agent 收到结构化结果决定怎么呈现

**F. Hook 触发（与 Tier 0 的 Hooks 体系联动）**

严格说不是独立"事件源"，而是 main loop 在 dispatch 任何事件前后插入的标准回调点。Hooks 体系正是基于这些回调点：

```
[event 入队]
    ↓
[PreTurn hook]    ← 例：检查时间，专注模式下丢弃事件（agent 主动沉默）
    ↓
[inner loop]
    ↓
[PreToolUse hook]   ← 例：危险工具要用户确认（Tier 2 的 Permission 就挂这里）
    ↓
[tool 执行]
    ↓
[PostToolUse hook]  ← 例：tool 结果脱敏后才喂回 LLM
    ↓
[PostTurn hook]   ← 例：把本轮新事实丢给 memory 抽取
                    （现有 _observe_turn 的去硬编码版本）
```

### 4.3 最小骨架（仅作示意，不是接口约定）

```python
class AgentRuntime:
    def __init__(self, conversation: Conversation):
        self._conv = conversation
        self._inbox: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._sources: list[EventSource] = []   # cron / monitor / user / bg_task / ...

    async def run(self) -> None:
        for src in self._sources:
            src.start(self._inbox)               # 各 source 异步往 inbox 塞事件
        while True:
            ev = await self._inbox.get()
            turn_input = self._translate(ev)     # 事件 → Conversation 能消费的形态
            if turn_input is None:
                continue                         # 该事件不需要 LLM 转（agent 选择沉默）
            for chunk in self._conv.stream(turn_input):   # 复用现有 inner loop
                self._dispatch(chunk)
```

三个新东西：

1. **EventSource 抽象**：`UserSource` / `CronSource` / `BackgroundTaskSource` / `MonitorSource` …… 统一往 inbox 塞 `AgentEvent`
2. **inbox（事件队列）**：单消费者串行 dispatch，保证一次只跑一轮、避免并发改 session
3. **`_translate(ev)`**：把非 user 类事件翻译成 Conversation 能吃的输入。最干净的做法是给 `Conversation` 加一个新入口（如 `dispatch_system_turn(prompt, kind)`），让 main loop 能塞"系统级触发轮"而不是伪装成 user_message

### 4.4 不需要重写已有代码

inner loop（`conversation.py` 已有）作为子例程被 main loop 调用，**不需要改动**。改造点集中在：

- 新增 `AgentRuntime` + `EventSource` 抽象
- 给 `Conversation` 加 `dispatch_system_turn` 类入口
- 把 `_observe_turn` 的硬编码挪到 PostTurn hook 注册

现有 `send` / `stream` 入口可保留（`UserSource` 内部走它），向后兼容。

## 5. 依赖关系图

```
Tier 0  [ Agent main loop ] ←捆绑→ [ Hooks 体系 ]
                │   │
                │   └─────────────────┐
                ↓                     ↓
Tier 2   [ 后台 / 异步 tool ]   [ Permission（PreToolUse hook） ]
                │
                ↓
Tier 2   [ Sub-agent 协作 ]


Tier 1（独立，不依赖 Tier 0）：
  - 流式协议补 reasoning / thinking 通道
  - 并行 tool 调用
  - ToolRegistry 运行时化 + MCP

Tier 3（方向性，独立）：
  - Plan / Confirm 状态机
```

## 6. 不在本文范围

- memory 系统本身的演进（在 `docs/explorations/memory/`）
- 上下文管理策略的细化（在 `docs/explorations/context-management/`）
- voice / frontend / bridge 层的补全
- 任何"是否做、什么时候做、谁来做"的决策——本文只盘点
