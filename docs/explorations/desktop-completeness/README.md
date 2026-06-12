# 桌面端补全（Desktop Completeness）

> 本文是过程笔记（exploration），与 [`engine-completeness/`](../engine-completeness/) 平行——这次站在桌面端（`frontend/`）的视角盘点缺口。**不锁定优先级、排期或具体接口**。真要做某一项时再去 `docs/decisions/` 或 `docs/requirements/` 立项。

## 1. 背景

参照对象一样是 Claude Code 级别的"产品级 agent"，但桌面端的参照系还多一层——`docs/explorations/desktop-pet-form-factor/` 调研的同类桌宠项目（clawd-on-desk / TinyRoommate / Codex Pet / NekoAI 等）共同浮现出的"桌宠标准交互"。

当前 agent-friend 的桌面端（`frontend/`）是 Tauri 2 双窗口（透明置顶 `pet` + 常规 `chat`）+ React 19 + AG-UI 自写 fetch-SSE 流式消费，跑通了"对话窗能聊"的最小闭环。但若要支撑 0001 vision 里"有人格的陪伴朋友"这个定位，桌宠这边还远没到能"成形"——多数关键交互在 pet 窗里完全缺位。

本文把这些缺口罗列、按重要性与依赖关系排序，并单独展开最大的一块——**桌宠侧消息呈现 + 跨窗口订阅 + bridge push 通道**这一组捆绑。

## 2. 现状：桌面端已经做到位的骨架

- **Tauri 双窗口**：pet（240×320 透明置顶无装饰）+ chat（420×640 常规），托盘 + 跨进程 `open_chat` invoke（`src-tauri/src/lib.rs`）
- **透明区穿透 plumbing**：Rust 侧 60fps `cursor_position` → 前端 `usePetPassthrough` + `[data-hit]` DOM 命中判定；spike 已留 Live2D alpha 采样的"缝"（`usePetPassthrough.ts:20`、`lib.rs:30`）
- **AG-UI fetch-SSE 流式消费**：自写 `runAgentStream` + `applyAguiEvent` reducer，已为 `THINKING_*` 事件预留接口（`stream.ts`、`conversationReducer.ts`）
- **历史会话投影**：从 bridge 事件流 project 出 `ChatMessage`，切走再切回不丢工具卡片（`sessionProjection.ts`）
- **拟人兜底**：`FRIENDLY_FALLBACK` + bridge RUN_ERROR 直接展示，不暴露技术细节
- **基线**：shadcn + tailwind 4 + zustand + axios，dev/prod 多入口 vite 配置
- **窗口管理基础动作**：左键拖拽桌宠移动窗口、关闭对话窗只隐藏不销毁、托盘三项菜单

## 3. 缺失模块清单

按 Tier 0 → Tier 3 排序。Tier 内不分先后，按依赖关系归类。

### Tier 0 — 桌宠形态之"魂"（必须先做，强耦合，**捆绑做**）

**桌宠侧消息呈现（speech bubble 气泡）+ 跨窗口 conversation 事件订阅 + bridge agent→桌面 push 通道**

- 当前 pet 窗完全不消费 conversation 事件，只是"打开 chat 窗的按钮"——跟所有调研对象的"桌宠本身就是主对话载体"模式不符
- engine main loop 主动发起轮（bedtime 提醒、anniversary 等）根本不该出现在 chat 窗（user 主动开窗才看到的临时面板），必须落在常驻可见的桌宠气泡上
- 三块共享同一个架构决定（"谁是 conversation 流的 owner、怎么广播"），分开做必然返工

→ 详见 §4。

### Tier 1 — 独立、收益直接（可单独做，不依赖 Tier 0）

**形象状态机**
idle / thinking / speaking / waiting / error / asleep 等 mood，目前是静态圆形 DOM。状态机本身可用占位贴图驱动，与 Live2D 接入解耦。**收益独立**——即便不接 engine 也能立刻改善"agent 在思考 / 在说话 / 离线"的可读性。

**跨 Space / 全屏浮动**
`tauri.conf.json` 当前未配 `visible_on_all_workspaces`、`window-level: floating`，桌宠不跟随用户跨虚拟桌面、不在全屏应用之上浮动，违反桌宠定位的"常驻在场"语义。纯配置 + Tauri 能力声明。

**bridge 连接连续性**
`connection.ts` 只 ping 一次，无重连、无周期心跳、无桌宠侧可视化反馈。bridge 掉线时桌宠表情应反映 offline（与上面的状态机自然耦合，但状态机做了它才有意义）。

**桌宠位置持久化 + 屏幕边界 clamp**
当前每次冷启动复位到 `tauri.conf.json` 初始坐标，且能拖出屏幕外。两件事都属于本地存储 + 拖拽边界 clamp 的小补，独立可做。

### Tier 2 — 依赖 Tier 0 或外部模块

**主动 nudge 的桌面端承接**
engine main loop 落地后会产生"非 user 触发轮"（bedtime / anniversary / idle 反思 / 后台任务回包等），这些消息到桌面端要走哪个出口——桌宠气泡？系统通知？声音？专注模式下的静默判定在前端做还是后端做？**必须先有 Tier 0**（呈现位 + push 通道）和 engine main loop（事件源头）。

**环境感知信号上行**
检测前台 app / 长时间无键鼠 / 系统通知拦截，作为 engine main loop `EnvSignal` 的供给侧。**整体 Tier 2，但内部要拆**：

- **采集 + 上行通道**：纯传感器，HTTP POST 给 bridge。**这部分可以独立先做**，不依赖 engine——属于"提前准备好原料"
- **环境信号 → 桌宠反应**（决策）：**严禁前端本地做** if-then 业务决策（"看到 90 分钟长焦点就切 worried"）。决策权必须在 engine 侧：engine 持有当前 persona（"懒"型 persona 会容忍久坐 / "严厉"型会催）、完整对话历史（半小时前用户说过"今天必须写完"）、memory（最近在赶 deadline）。前端若先做一套本地决策，等 engine main loop 接 `EnvSignal` 时这套逻辑要被搬走，期间还会出现"前端切了 worried、engine 又下发 idle"的双层决策打架

外加 macOS Accessibility 权限申请流，技术上不轻。

**右键菜单 + 双击反应**
调研里反复出现的桌宠标配（隐藏 / 切尺寸 / 更多操作）。当前桌宠只支持左键拖拽。可独立做，但内容（菜单项里放什么）多数依赖其他模块（persona 切换、勿扰模式等），所以放 Tier 2。

**错误状态的桌宠表现**
bridge 掉线 / 工具失败 / 触达 tool turns 上限 → 桌宠表情/动作应该有差异化反馈。当前所有错误都只是 chat 窗里一行兜底文案。依赖 Tier 1 的形象状态机（要有表情切换通道）+ 错误信号源（部分还要 bridge 主动 push）。

### Tier 3 — 方向性，看产品决定

**Live2D 真实形象接入**
spike 已挖好坑（`usePetPassthrough` 的 hitTest 可换成对 PixiJS canvas 的 `gl.readPixels` alpha 采样，plumbing 不变）。但接入本身是大件：美术资源、动画绑定、参数驱动、性能调优。形象状态机做完后再决定是否升级。

**托盘菜单扩展（勿扰 / 切 persona / 形象尺寸 S/M/L）**
调研里的桌宠标配。但切 persona 依赖 persona 接入（你明确暂缓）；勿扰与主动 nudge 强耦合（依赖 Tier 2）。

**打包发行 / 自动启动 / macOS 公证签名**
`frontend/README` 写明"生产打包不在本期范围"。终归要做，但属于发布工程，不影响功能完整性。

**跨平台窗口能力差异（Windows / Linux）**
透明窗、置顶、跨 Space、cursor passthrough 在三平台行为各异，目前只 spike 过 macOS。要做"跨平台桌宠"时再补，本期可不动。

## 4. Tier 0 详细展开：桌宠侧消息呈现 + 跨窗口订阅 + bridge push 通道

### 4.1 现状的不自然之处

`frontend/src/pages/pet/App.tsx` 全文不到 50 行，pet 窗只做三件事：拖拽窗口、显示占位形象、点按钮调 `open_chat` invoke 弹 chat 窗。**conversation 事件流（user / assistant / tool）完全跟 pet 无关**——它们只在 chat 窗的 `useConversationStore` 里被消费、只在 `MessageList` 里被渲染。

这意味着：

- 用户必须打开 chat 窗才能看到 agent 说什么 → 桌宠等于一个**带形象的启动器**
- engine main loop 一旦落地，主动发起的"很晚了该睡了"消息**没有呈现位**（chat 窗没开、push 到 chat 窗用户也看不见）

调研里所有桌宠（clawd / TinyRoommate / NekoAI / Codex Pet）都把 **speech bubble 气泡作为主对话载体**——agent 说话直接冒在桌面上，不需要专门开窗。这恰好对应 0001 vision 里"有人格的陪伴朋友"的"在场感"。

### 4.2 为什么三块必须捆绑

**单做气泡**（只在 pet 窗加一个气泡 UI 组件）→ 数据从哪来？让 pet 窗自己再跑一遍 `runAgentStream` 吗？两个窗口各自 POST `/ag-ui/run`，bridge 那边会建两个 turn，**会话直接分叉**。所以气泡必须先解决"pet 窗如何看到 chat 窗发起的那一轮 conversation 流"。

**单做订阅**（只把事件路由到 pet 窗）→ 订阅到了消息没地方渲染，等于白做。

**折中"pet 窗 zustand store 镜像 chat 的 messages 数组"** → 是死胡同。这种做法解决不了"main loop 主动发起、chat 窗没开"的场景——主动发起的事件源**不在 user 输入路径上**，没有 chat 窗去"先消费"再让 pet 镜像。等到要做主动 push 时还得回头拆掉。

三块共享同一个核心架构决定：**conversation 事件流的 owner 是谁、怎么广播给两个 webview**。这个决定一旦定下，三块都要在上面长出来；分开做意味着前两块用一套 owner 模型、第三块再换一套，必然返工。

### 4.3 三块的具体边界

**(a) 前端架构：chat / pet 共享同一份 conversation 流**

典型选择是定一个 owner，把 SSE 事件通过 Tauri IPC 广播给两个 webview。可能的形态（不在本文锁定）：

- **chat 窗作 owner**：保持现有 `runAgentStream` 在 chat 窗，通过 Tauri `emit_to("pet", ...)` 把事件广播给 pet 窗。简单，但 chat 窗未开时无 owner——需要单独处理"无窗 owner"分支
- **Rust 侧作 owner**：Rust 侧常驻订阅 bridge 的 SSE，两个 webview 都从 Rust 订阅。统一，但要把现在 TS 侧的 reducer 逻辑搬一层
- **拉一个 hidden background webview 作 owner**：折中——chat / pet 都可见时各自渲染，但 owner 是第三个不可见 webview

每种选择都有取舍，本文不锁定。

**(b) 桌宠 UI：气泡组件**

至少要解决：

- 位置（贴形象上方？下方？跟随屏幕边缘自动翻转？）
- 动画（冒出 / 停留 / 消失，对应 assistant 流式逐字 vs 主动消息整段出）
- 超长截断与展开（气泡不能无限长；点击展开 → 开 chat 窗？保留在气泡内滚动？）
- 消失策略（用户读完后多久消失？被点击会停留？被新消息打断怎么处理？）
- 点击穿透到形象的处理（气泡区是 `[data-hit]`，但拖拽形象时气泡不该挡）
- 多消息排队（两条主动消息几乎同时来时怎么显示）

**(c) bridge 协议：agent → 桌面 push 通道**

当前 bridge 是 **pull 模式**：客户端 POST `/ag-ui/run` 才有流。这只支持"user 输入触发"。engine main loop 落地后会有"agent 主动发起轮"——**没有 user 输入也要向桌面推消息**。bridge 那边需要：

- 一个客户端可订阅的常驻通道（长 SSE 或 WebSocket，本文不锁定具体形态）
- 协议上区分"user 触发轮的流"与"agent 主动轮的流"，前端据此决定是否要冒气泡（专注模式下可能丢弃后者）

这条是 Tier 0 跟 engine main loop 在**协议层**的对接点——engine 那边做事件源（cron / 后台任务回包 / EnvSignal callback），bridge 这边做协议出口，桌面端这边做接收 + 呈现。三个模块必须在同一时期一起设计，否则协议会被先做的那两边各自定一遍。

### 4.4 不需要重写已有代码

现有 `runAgentStream` / `applyAguiEvent` reducer / `useConversationStore` 都可保留。改造点集中在：

- 新增一层"事件 owner"抽象（不论选哪种 owner 形态），让 chat 窗的 store 从订阅 owner 取事件，而不是直接消费 fetch-SSE
- 新增 pet 窗的气泡 store + UI 组件，同样订阅 owner
- bridge 新增 push 通道；前端 owner 同时订阅"user 触发流"和"主动 push 流"

这跟 [`engine-completeness/`](../engine-completeness/) §4.4 的"inner loop 不需要重写"是同一种增量风格——在已有数据通路上插一层，而不是推倒重来。

## 5. 依赖关系图

```
Tier 0  [ 桌宠气泡 + 跨窗口订阅 + bridge push 通道（捆绑） ]
            │   │
            │   └────────── 跨文档依赖 ──────────► engine main loop（见 engine-completeness）
            │
            ↓
Tier 2   [ 主动 nudge 的桌面端承接 ]
Tier 2   [ 环境感知信号上行（决策侧依赖 engine；采集侧可先做） ]
Tier 2   [ 错误状态的桌宠表现 ] ───依赖───► [ 形象状态机（Tier 1）]


Tier 1（独立，不依赖 Tier 0）：
  - 形象状态机
  - 跨 Space / 全屏浮动
  - bridge 连接连续性
  - 桌宠位置持久化 + 边界 clamp

Tier 3（方向性，独立或依赖外部）：
  - Live2D 形象接入（依赖 Tier 1 形象状态机）
  - 托盘扩展（依赖 persona / 勿扰）
  - 打包发行 / 自动启动 / 公证签名
  - 跨平台窗口能力差异
```

## 6. 不在本文范围

- **persona 接入桌面端**：用户明确暂缓（定位未完全想清楚，且优先级低于跑通主流程）
- **agent 编排层的缺口**：在 [`engine-completeness/`](../engine-completeness/)，本文只在依赖图里引用其交付物
- **voice_bridge 接桌面端**：属于通道层（IM / 语音 / CLI 之外的另一条通道），不是桌面端本身的缺口
- **美术资源生产**：Live2D 模型、表情贴图、动画曲线等
- 任何"是否做、什么时候做、谁来做"的决策——本文只盘点
