# 桌宠活人感 + 主动事件抽象（Pet Liveliness and Proactive Events）

> 本文是过程笔记（exploration），探讨当前 Live2D 桌宠"形象在场但缺乏互动"的问题，并梳理三类活人感杠杆 + 一套"主动事件 / 外部感知事件"统一抽象。**不锁定优先级、排期或具体接口**。真要做某一项时再去 `docs/decisions/` 或 `docs/requirements/` 立项。

## 1. 背景

018 接入 Live2D + 4 态状态机后，桌宠"看起来活了"，但实际感受是"独立的 Live2D 形象，跟我没什么互动"。本次探索的目标：梳理让桌宠真有"活人感"还差哪些事件 / 反馈 / 链路，以及它们各自归属哪一层。

## 2. 活人感的三个方向

按 ROI 从高到低排，每个方向独立可做：

### 2.1 视线 / 头部跟随光标

把鼠标光标位置作为 Live2D `ParamAngleX/Y/Z + ParamEyeBallX/Y` 的低通滤波输入。纯渲染层改动，不动业务管道。**视觉立竿见影，但本质是被动反应**。

### 2.2 触摸反馈：点击 / 拖拽触发 motion + 表情切换

点击 head / body 不同 hit 区触发 Live2D 自带 motion + 临时态（`reacting`），拖拽给"被拎起来"反馈。复用现有 4 态机，叠加临时态或在 idle 上叠 motion。**真互动闭环，但依赖模型 motion 资源**（Hiyori 自带 motion / expression 是否够细分 head / body 区需 audit）。

### 2.3 Agent 主动事件

长时间无互动 / 系统信号 / 时间到点 → agent 主动开口。**真"活人感"，但策略层最难**——节奏不对就从"陪伴"变"骚扰"。本文 §3-5 主要展开这块。

## 3. 主动事件的现状

bedtime 这一链路已经 ready 但默认关：

- `agent/src/agent/runtime/sources.py:111` 的 `BedtimeSource`：独立 thread，到点塞 `SystemTriggerEvent(source_kind="cron:bedtime")` 进 inbox
- → `Conversation.dispatch_system_turn` 注入 trailing system addendum → LLM 出 assistant_message
- → 014 push channel → frontend `petBubble` store → PetBubble 窗口呈现
- 默认 `enable_bedtime: bool = False`（`agent_bridge/src/agent_bridge/settings.py:66`），注释："主动陪伴属于产品决策，没显式开启不应该自己冒出来"
- 另一个 `IdleReflectionSource`（D 类 silent / `output_visibility="memory_only"`）同样默认关

**已知体验 bug**：`docs/issues/006-bedtime-prompt-history-hijack/` 还 open——bedtime fire 时 LLM 被 conversation history 末尾拽偏，模拟 user 口吻答复而不是真主动提醒。issue 内已给三个修复方向（强化 prompt addendum / 加 separator / few-shot），首推方向 A。

**抽象层面的关键认识**：`EventSource + SystemTriggerEvent + dispatch_system_turn + push channel` 已经构成一个完整的"主动事件"框架，新增触发源 = 加一个 source class + 注册，不动协议。

## 4. 交互事件分层

按"信号在哪发起 / 谁消费 / 是否进 agent inbox"分五类。这次讨论中**很可能要做的**作为核心清单，其他作为种子库附录。

### A 类 · 引擎层发起（agent.runtime.sources）

进程内塞 inbox，不经网络。本质是 `cron:*` 参数化。

| 事件 | 状态 | 触发 | 输出可见性 |
|---|---|---|---|
| `cron:bedtime` | 已有，默认关 | 每日固定时刻 | user |
| `idle_reflection` | 已有，默认关 | idle 阈值 | memory_only |
| `idle_chat` | 待加 | 长时间无互动 | user |
| `cron:morning` | 待加 | 早晨问候 | user |

### B 类 · 桌面端 UI 输入，直接消费（纯 frontend）

完全在 webview 内闭环，**不出进程、不进 agent inbox**，延迟最低。

| 事件 | 触发 | 消费 |
|---|---|---|
| 视线 / 头部跟随光标 | mousemove（低通滤波） | Live2D `ParamAngleX/Y` + `ParamEyeBallX/Y` |
| 点击 head / body | click + alpha hit-test 分区 | Live2D motion + 临时态 `reacting` |
| 拖动反馈 | drag start / move / end | 角度叠加 + 表情切换 |
| 长按 / 抚摸 | mousedown 持续 | 表情切换（舒服） |
| 边缘"踩墙" | overlay 位置贴边 | idle motion 偏移 |

### C 类 · 桌面端 UI 反向上报 agent

用户对桌宠的某些累积互动需要让 agent 知道，通过新增反向 HTTP 端点入 inbox。**此链路本期还不存在**——017 / 018 的 cursor feed 是 frontend → bridge 单向，但仅用于 hit-test / 光标透传，不进 inbox。

| 事件 | 触发 | Agent 用途 |
|---|---|---|
| `interaction:tap_n` | 用户点击桌宠累计 N 次 | pet 反话 / 卖萌 |
| `interaction:long_idle` | 用户对桌宠长时无互动 | 主动找你 |

### D 类 · OS sensing 反向上报 agent

`frontend/src-tauri` 拿到 OS 信号，**直接 HTTP 到 bridge**（不经 webview 中转），走与 C 类相同的反向入口。

| 事件 | 触发 | 用途 |
|---|---|---|
| `system:app_launched` | bridge / overlay 进程启动完成 | 打招呼（刚打开） |
| `system:wake_from_lock` | macOS `NSWorkspaceDidWake` / Win `WM_POWERBROADCAST` | "欢迎回来" |
| `system:foreground_app_change` | macOS NSWorkspace activeApp / Win `GetForegroundWindow` | 摸鱼 / 工作切换吐槽 |
| `system:battery_low` | OS power | 提醒插电 |
| `system:network_offline/online` | OS net | 网断 / 恢复反馈 |
| `system:quiet_hours_signal` | 全屏游戏 / 会议检测 | **抑制器**（喂给策略层），非触发 |

### E 类 · 外部协作者 / 工作流状态感知

桌宠从本机其他 agent / 开发工具的状态流里获得环境感知。首个明确候选是 Codex app-server：它提供 thread / turn / item / approval 等事件流，适合让桌宠知道"旁边的 Codex 正在思考、跑命令、等审批、完成或失败"。

默认路径是 **src-tauri 订阅 → webview 直接消费**，不进 agent inbox；只有少数需要"开口"的状态才升级成 `SystemTriggerEvent`，走与 D 类相同的反向入口。

| 事件 | 触发 | 默认消费 | 是否升级入 inbox |
|---|---|---|---|
| `codex:turn_started` | Codex turn 开始 | 桌宠进入观察 / 专注态 | 否 |
| `codex:item_started:command` | Codex 开始跑命令 / 工具 | 桌宠进入工作中 / 思考态 | 否 |
| `codex:waiting_on_approval` | Codex 等用户审批 | 视觉提醒 / deterministic bubble | 超过阈值后可升级 |
| `codex:turn_completed` | Codex turn 完成 | 放松动作 / 短提示 | 可选，默认否 |
| `codex:turn_failed` | Codex turn 失败 | 轻微 error / 关切态 | 可升级 |

关键边界：

- 不默认读取 prompt、命令输出、diff、assistant 文本内容；优先只消费状态类型、时间、是否需要用户动作。
- 不把每个 Codex 事件都变成 agent 主动开口；桌宠先"陪着反应"，再由策略层挑少数高价值事件说话。
- 不归入 B 类：B 类是"用户摸桌宠 → 桌宠即时反应"；E 类是"桌宠感知外部协作者正在工作 → 桌宠环境反应"。

### 种子库（扩展候选，真要做时再评估）

不在核心清单内，但讨论中提到、按五类原则归位的扩展事件：

- **A 类**：`cron:hourly` / `cron:special_date`（生日、节日）——A 类已是 `cron:*` 模式，新增等同参数化
- **B 类**：双击 / mouseleave（光标离开 → idle）/ window blur（用户切到别处）——B 类是连续视觉空间，列代表性几个足以说明类型，落地按需加
- **C 类**：`interaction:played_with_long`（被把玩很久，`long_idle` 的反方向同质事件）
- **D 类**：`system:app_will_quit`（道别）、`system:power_plugged/unplugged`（充电状态）、macOS Do Not Disturb / Win Focus Assist（作为 `quiet_hours_signal` 的具体 OS 来源）
- **E 类**：`external:github_ci_running/done`、`external:build_failed`、`external:calendar_meeting_soon`（若来自本地授权 connector / app-server，而非 OS 原生信号）

新增事件按五类判定原则归位：
- 谁产生（agent 内 / UI / OS / 外部工具或协作者）
- 在哪里消费（frontend 内闭环 / agent inbox）
- 是否经反向 HTTP 上报

## 5. 关键架构判断

### 5.1 所有"要 agent 起一轮"的信号统一从 inbox 进

A / C / D 三类信号最终都长成 `SystemTriggerEvent`，通过 `agent.runtime.inbox` + `dispatch_system_turn` 这一统一聚合点进入 agent 主循环。E 类只有在需要 agent 开口时才升级成同一形态；默认只是桌宠本地状态源。**统一点不是新加一层做分发，而是抽象本身就锚定在 inbox**。

```
A (引擎层内部源):       BedtimeSource ──► inbox.put(SystemTriggerEvent)
B (UI 直接消费):       mousemove ──► Live2D 参数  [完全在 frontend，不出进程]
C (UI 反向上报):       click ──► frontend ──HTTP──► bridge ──► inbox
D (OS sensing 上报):   src-tauri ──HTTP──► bridge ──► inbox
E (外部工作流感知):     codex app-server ──► src-tauri ──► webview 直接消费
                                         └─[少数事件升级]──HTTP──► bridge ──► inbox
                                                          │
                                                          ▼
                                                    policy gate
                                                          │
                                                          ▼
                                                 dispatch_system_turn
```

**唯一例外是 B 类**——纯 UI 视觉反馈，不出进程、不进 inbox。当 B 类累积到需要 agent 知道（连点 N 次、被把玩很久），**升级为 C 类**走反向上报；升级后仍然是同一抽象。

E 类是第二个例外：它不是 UI 输入，而是外部状态流；但默认也不进 inbox。它和 B 类共享"低延迟直接消费"这个消费模式，和 D 类共享"由 src-tauri 持有外部感知能力"这个实现位置。

### 5.2 OS sensing 的上报路径：src-tauri 直接 HTTP 到 bridge

不绕道 webview，理由：

- **少一跳**进程边界往返
- **跟 C 类共用同一个反向上报网络入口**（bridge 新增的 HTTP 端点，姑且叫 `POST /internal/system-trigger`，本期还没有）
- **为将来 `client/` 若立起来留接口边界**——sensing 代码搬到独立进程后，只要保持同样的 HTTP 契约，bridge / agent / frontend 一行不动

### 5.3 E 类的默认实现：先做 ambient，不急着让 agent 开口

Codex app-server 状态感知的第一版应当只做 ambient 反应：

1. `frontend/src-tauri` 新增 `codex_status_subscriber`，作为本地 app-server client。
2. Rust 把状态压成窄事件：`codex://activity`，只含状态种类、thread / turn id、是否需要用户动作、粗粒度 item kind。
3. pet webview 新增 `codexActivityStore` / `codexActivityPolicy`，把事件投影为桌宠状态、motion、deterministic bubble。
4. 只有 `waiting_on_approval` 超时、`turn_failed` 等少数事件，经独立阈值和用户开关后，才走 `POST /internal/system-trigger` 进入 agent inbox。

这样做的好处：

- 桌宠能"察觉 Codex 在忙"，但不会抢用户注意力。
- 不依赖 hooks trust review。
- 不默认读取或复述 Codex 的具体内容，隐私边界更干净。
- 后续若要支持 GitHub CI、构建任务、日历会议等状态源，也能复用同一 E 类接口。

### 5.4 `client/` 短期不立、中长期保留

`docs/decisions/0002` §3.8 设定 `client/` 是"操控电脑"（主动 do）的占位，孵化期不实现。本期 sensing 不需要立 `client/`——`frontend/src-tauri` 已是 Rust 进程，挂 OS hook 几十行代码就够。

但中长期建议保留这一层，**除文档已写的"崩溃隔离" / "Rust 工具链一致"外，补一条更硬的理由**：

- **权限边界**：macOS Accessibility / Screen Recording / Input Monitoring 是**按 binary 授权**的
- 桌宠和"操控电脑"共用一个 binary，等同于把这些权限交给整个 webview 进程
- 桌宠常驻、加载远端资源、跑 LLM 输出、未来可能接 IM / 插件，攻击面比纯系统操控大得多
- 把"操控电脑"放在独立 binary，用户单独授权那个 binary，**桌宠永远不要这些高权限**——这是用户信任 AI 朋友的必要条件，不是工程洁癖

可考虑在 `docs/decisions/0002` §3.8 备注或新立 ADR 补这条理由。

### 5.5 策略层放在 agent.runtime dispatcher 入口前

不论事件多丰富，以下四点不做就一定变骚扰：

1. **静默时段**——全屏 / 会议时整体抑制（消费 D 类 `quiet_hours_signal`）
2. **最小间隔**——任意两次主动事件 ≥ N 分钟
3. **优先级**——多 source 同时 fire 时合并 / 丢弃（如 `wake_from_lock` 优先于 `cron:morning`）
4. **每源开关**——设置面板独立开关（019 ActionBar 设置壳已就位）

**位置**：`agent.runtime` 的 dispatcher 入口前一个 policy gate——所有 source 入 inbox 后，dispatcher 取出来时先过 gate（`drop` / `defer` / `pass`）。一处过滤覆盖所有 source，不用每个 source 各自实现节流。

**frontend 是否要做副本**（用户开关前置避免无效信号 / quiet_hours 视觉同步）：**保留，本期不敲定**，目前场景看起来不太需要。

## 6. 建议节奏

不锁定排期，只给做的时候的依赖关系：

1. **B 类（视线跟随 + 触摸反馈）**——纯 frontend，最快出"活人感"
2. **E 类 Codex app-server ambient sensing**——先不入 inbox，只驱动桌宠观察 / 专注 / 等待审批 / 完成 / 失败等状态
3. **D 类的 `system:app_launched` + `wake_from_lock`**——Tauri 几十行 Rust；同时铺一个反向 HTTP 上报端点
4. **A 类 `idle_chat`**——复用现有 `EventSource` 抽象
5. **策略层骨架**——上面 2-4 任意一个要让 agent 开口时都会撞到，顺手立起来；具体接口由"第一个真正落地的反向上报事件"逼出来，避免空骨架
6. **D 类其余 + C 类反向上报 + E 类升级入 inbox**——等前 5 项验证产品节奏后再扩

bedtime 的 `enable_bedtime` flag + issue 006 的 prompt 修复独立于上述节奏，由用户单独处理。

## 7. 不在本文范围

- **issue 006 的具体修法**——见 `docs/issues/006-bedtime-prompt-history-hijack/`
- **B 类的具体 motion 资源 audit**（Hiyori 自带 motion / expression 是否够分 head / body 区）——立 feature 时评估
- **bridge 反向 HTTP 端点的具体 schema**（`POST /internal/system-trigger` 只是占位名）——立 feature 时设计
- **Codex app-server client 的具体协议适配**（初始化、线程选择、重连、是否连接已有 desktop session）——立 feature 时 spike
- **策略层的具体规则集**（静默时段判定算法、最小间隔默认值、优先级表）——立 feature 时设计
- **任何"是否做、什么时候做、谁来做"的决策**——本文只盘点
