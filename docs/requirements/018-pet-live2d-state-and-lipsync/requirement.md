# 018 · 桌宠 Live2D 接入 + 状态机 + Codex 兼容 + lip-sync（pet live2d state and lipsync · 17b 形象本体）

> Pet Live2D, State Machine & Lip-sync — 17b Avatar Body
>
> 在 [需求 017](../017-pet-overlay-form-switch/) 17a 落地的"整屏 transparent overlay + PIXI canvas + 可替换 avatar-slot Container + sprite world position 数据流 + 操作栏 sprite-relative 浮动 UI 层"底座之上，把"占位形象（Graphics + Text）+ 单态 idle"升级为"真 Live2D 形象 + 多态状态机（idle / thinking / speaking / error）+ Codex 兼容（015 push event flow → 状态机驱动）+ lip-sync（speaking 态下口型驱动）"。本期是 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) 锁定的路线 A 第二步——**把形象本体从占位升级为活的 Live2D 桌宠**，沿 17a design §7 沉淀的 5 个 17a/17b 接缝点接力。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

- [需求 017 / 17a](../017-pet-overlay-form-switch/) 已落地：整屏 transparent overlay（macOS NSPanel + Win 整屏 alwaysOnTop）+ PIXI v8 canvas + **PIXI Container("avatar-slot") 作可替换形象容器**（内部 children 由 `Graphics(circle) + Text("占位形象")` 占位）+ sprite world position 数据流 + 操作栏 sprite-relative DOM 浮动 + alpha hit-test plumbing；macOS 7/12 AC 通过，Win 端代码已落 + cargo check / build 通但真跑 AC 留 17b 阶段补；issue 007 / 008 关闭。
- [17a design.md §7](../017-pet-overlay-form-switch/design.md#7-17a17b-接缝点显式列表供-17b-接力) 沉淀了 5 个 17a/17b 接缝点（avatar-slot children 替换 / sprite world position 数据流 / cursor alpha hit-test / 状态机 hook 点 / 操作栏 hover bridge），其中 **#1 + #4 是 17b 动点**，#2 / #3 / #5 不动。
- [需求 015](../015-desktop-pet-bubble-and-conversation-owner/) 落地 bridge push 通道桌面端消费侧：Rust `push_subscriber` 解码 envelope → `emit_to("pet", "agent://push", env)` → `frontend/src/stores/petBubble.ts: startPetBubbleSubscriber()` 单点订阅 → `usePetBubbleStore.ingest(env)` → `petBubblePolicy` 决定是否冒气泡。envelope.events 是 ConversationEvent 流（`text_delta` / `tool_call_request` / `tool_call_result` / `done`）。
- [需求 007](../007-voice-call/) 立项的 voice_bridge 模块当前**代码层未实装**——`voice_bridge` / `audio_output` 在 frontend / agent / agent_bridge 全仓 grep 无引用（仅 spike 阶段）；本期 17b 不接 voice，但 lip-sync 接口必须为未来接入 voice_bridge audio 流留好扩展位。
- [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §1.1 最初的"Live2D 9:16 装不进 240×320 固定窗"问题已由 17a 整屏 overlay 路径解决；本期是 0004 §1.1 motivating problem 的产品形态落地——**真正把 Live2D 摆上桌面**。

### 1.2 这次要做什么

按 17a design §7 接缝点 + 用户对话拍板的方向，把"占位形象 + 单态 idle"升级为"真 Live2D 形象 + 多态状态机"：

- **Live2D 接入**：在 avatar-slot Container 内 children 由 `Graphics + Text` 占位换为 `Live2DModel`；选 PIXI v8 兼容的 Live2D 库（design 阶段选型 + 撞墙 fallback）；Cubism Core JS 挂载 + `.model3.json` 多文件资源通过 Vite asset pipeline ship；模型在 slot 内显示 / 缩放 / 锚点对齐；外层 Container plumbing（drag / world position / hit-test / 操作栏 hover bridge）一律不动。
- **桌宠状态机最小集**：`idle` / `thinking` / `speaking` / `error` 4 态；新建 `petStatePolicy` + `usePetStateStore`，与 015 `petBubblePolicy` + `usePetBubbleStore` 对称；FSM 投影关心"agent 在做什么"，与 015 气泡显示态（`idle / showing / expanded`，关心"用户能不能看到气泡"）**完全正交**。
- **Codex 兼容（push event flow → 状态机驱动）**：新建独立 `listen("agent://push", ...)` 订阅器（不分叉 015 现有 subscriber），envelope 同源消费、各管各——`tool_call_request` / `tool_call_result` 进 thinking 态、`text_delta` 进 speaking 态、`done` 回 idle 态、SSE / push 通道错误进 error 态。状态变更派发到 Live2DModel motion / expression。
- **lip-sync · 文本 cadence 驱动 + voice 通道扩展位**：抽象 `MouthDriver` 接口（输出 `mouthOpenY: 0..1`），本期落 `TextCadenceMouthDriver`（按 `text_delta` 节奏 / token 时长打节拍）；speaking 态 enter 时 attach driver、exit 时 detach；接口签名 + driver 切换钩子留好，未来接 voice_bridge audio 流时新增 `AudioRmsMouthDriver` 即可，不动调用方代码（[issue 见 §1.4](#14-与-015--007-的关系)）。
- **17a 接缝点 #2 / #3 / #5 不动**：sprite world position 数据流（slot.x/y 由状态机 / motion 系统更新；emitSpritePos hook 仍挂 slot）、cursor alpha hit-test target（仍指 slot 区域；Live2D 内部 alpha 透明区由 SDK 渲染）、操作栏 hover bridge（slot pointerover/out → React `setHoverActionBar`）保持现状作为本期"零退化"AC 验证项。
- **跨平台 first-class**：macOS + Win 同期 first-class（沿 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §3.1 + 17a 已建立的双端 plumbing）；17a Win 端没真跑过的 AC（17a progress.md M17.9）顺手在 17b 阶段补真机回归。
- **不立前置 spike**：用户拍板"直接进 design 选库，撞墙时 fallback"——design 阶段选型时把 fallback 路径（如 `pixi-live2d-display` PIXI v8 兼容 fork 不通 → 切 `easy-live2d`）写进风险段；实施期撞墙现场切方案。

### 1.3 与 17a 的关系

17a 是 0004 路线 A 工程落地第一步（**承载形态切换底座**），17b 是 0004 路线 A 工程落地第二步（**形象本体替换 + 状态机驱动**）。17a design §7 5 接缝点明确划分：

| 接缝点 | 17a 落地 | 17b 动作 |
|---|---|---|
| #1 形象内容（avatar-slot 内 children） | `Graphics(circle) + Text("占位形象")` | **替换为 `Live2DModel`**（slot 外层不动） |
| #2 sprite world position 数据流 | drag → invoke `update_sprite_pos` | **不动**（slot.x/y 由 17b 状态机 / motion 更新；emitSpritePos hook 仍挂 slot） |
| #3 cursor alpha hit-test target | PIXI alpha readPixels / bounds 兜底 | **不动**（采样目标仍是 slot） |
| #4 状态机 hook 点 | slot 上预留 `slot.label`，单态 idle 无切换 | **接 015 push event flow → `setState(idle/thinking/speaking/error)` → 派发到 Live2DModel motion / expression** |
| #5 操作栏 hover bridge | slot `pointerover/out` → React `setHoverActionBar` | **不动** |

机制层：015 push 通道 / owner / sessionProjection / 016 bubble window / 17a sprite world position 数据流 / 17a 操作栏 plumbing / 17a cursor passthrough **全部不动**；唯一动的是 avatar-slot 内 children 替换 + slot 上挂状态机 + 新增独立 push 订阅器。17a 全自动化门禁 + macOS 已通过 AC 在本期完成后必须**全部回归通过**。

### 1.4 与 015 / 007 的关系

- **与 015 关系**：015 `petBubblePolicy` 关注"事件 → 哪种气泡内容"（**内容路由**），17b `petStatePolicy` 关注"事件流 → 桌宠当前态"（**FSM 投影**），两者正交——agent thinking 时气泡可能 idle；agent done 后气泡可能仍 showing。两个 policy 各自走独立 `listen("agent://push", ...)` 订阅（tauri event 是广播、cost 可忽略），各自 store、各自测试。015 任何代码 / store / policy / subscriber **不动**。
- **与 007 关系**：007 voice_bridge 当前未实装，本期 17b lip-sync 走文本 cadence、**不接 voice**；但接口形态留好扩展位（`MouthDriver` 抽象 + driver 切换钩子），未来 voice_bridge audio 流接入时只新增 `AudioRmsMouthDriver`、不改 lip-sync 调用方。

### 1.5 跨平台定位

agent-friend 跨平台桌面应用，**Windows = first-class 平台、不是 fallback**（沿 [ADR 0002](../../decisions/0002-incubation-tech-stack/README.md) §3.1 + 17a §1.6）。

17a 阶段 Win 端代码已落 + 自动化门禁通过 + Win spike 真机段实证可工程化，但 17a progress.md M17.9 "Win 端到端 AC-1 ~ AC-12 本期不跑"。17b 阶段：

- **17a Win 真机 AC 补回归**：17a 12 条 AC 在 Win 真机跑一遍（pet 主窗整屏 / PIXI canvas / 占位视觉等价 / sprite drag / bubble 跟随 / 操作栏 hover gate / F11 fullscreen 浮层 / 既有路径回归 / cross-build 全绿）作为 17b 进度依赖项（17b Live2D 形象呈现依赖 17a 底座正确）。
- **17b 新增 AC 同期 macOS + Win 验**：Live2D 模型加载 / 4 态状态机切换 / push event 驱动 / lip-sync 文本 cadence 节奏 macOS + Win 双端同期验，**不接受 Win 走 fallback**。

Linux 沿 0002 §3.1 + 0004 §3.1 不在范围。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **Live2D 模型加载与渲染** | 在 17a avatar-slot Container 内 children 由 `Graphics + Text` 占位替换为 `Live2DModel`；PIXI v8 兼容 Live2D 库（具体选型由 design 决定 + 撞墙 fallback 路径登记）；Cubism Core JS 挂载 / `.model3.json` 多文件资源 Vite asset pipeline ship；模型在 slot 内显示 / 缩放 / 锚点对齐与 17a 占位视觉**几何位置等价**（slot 中心仍是模型锚点，drag / bubble 跟随 / 操作栏锚点不动） |
| **桌宠状态机最小集** | 4 态 `idle` / `thinking` / `speaking` / `error`；新建 `usePetStateStore` + `petStatePolicy`，与 015 双件对称；state 切换派发到 Live2DModel motion / expression（具体 motion / expression 命名 + fallback 由 design 决定） |
| **Codex 兼容 · push event → 状态机驱动** | 新建独立 `listen("agent://push", ...)` 订阅器（不分叉 015 subscriber）；envelope.events 解析 → 状态切换：`tool_call_request` / `tool_call_result` → thinking；`text_delta` → speaking；`done` → idle；SSE / push 通道错误（具体识别策略由 design 决定）→ error；015 完全不动 |
| **lip-sync · 文本 cadence 驱动 + voice 扩展位** | 抽象 `MouthDriver` 接口（输出 `mouthOpenY: 0..1`）；本期落 `TextCadenceMouthDriver`（按 `text_delta` 节奏 / token 时长打节拍）；speaking 态 enter 时 attach driver、exit 时 detach；接口 + 切换钩子为未来 `AudioRmsMouthDriver`（接 voice_bridge audio 流）预留 |
| **17a 接缝点 #2 / #3 / #5 零退化** | sprite world position 数据流（slot.x/y 由 17b motion / 状态机更新 / emitSpritePos hook 仍挂 slot）/ cursor alpha hit-test target（采样目标仍是 slot；Live2D 内部 alpha 透明区由 SDK 渲染）/ 操作栏 hover bridge（slot pointerover/out → React setHoverActionBar）全部保持 17a 现状 |
| **17a Win 真机 AC 补回归** | 17a 12 条 AC 在 Win 真机跑一遍（17a progress.md M17.9 留下来的"17b 阶段补"）作为 17b 进度依赖项 |
| **既有路径回归** | 015 全 9 AC + 016 全 12 AC + 17a 全 12 AC（macOS 已通过的 7/12 + Win 补回归后的 12/12）在本期完成后**全部回归通过** |

---

## 3. 非目标（Out of Scope）

以下本期**明确不做**，避免范围蔓延：

- **真 audio mel/RMS lip-sync**：本期 lip-sync 仅走文本 cadence 驱动，不接 voice_bridge audio 流；`AudioRmsMouthDriver` 实现 + audio decode + FFT 滑窗 + 与火山 RTC PCM 流对接留作后续需求（17c / 与 007 voice 桌面接入耦合）。
- **voice_bridge 接入桌面**：007 voice_bridge 当前代码未实装；本期不引入 voice_bridge HTTP 调用 / 不在桌面端接 voice surface（沿 007 §3 "本期 voice 不接桌面 surface"）。
- **多 Live2D 模型切换 / 模型市场 / 模型切换 UX**：本期固定一个内置模型（具体模型由 design 选 + 资源 ship 策略由 design 决定）；多模型切换留后续需求。
- **模型滚轮缩放 / 缩放范围调节**：沿 17a 既有 slot 缩放语义（slot 大小由 design 决定固定值），不引入用户级缩放交互（OLV `kScale` / `MIN_SCALE` / `MAX_SCALE` 模式不本期做）。
- **桌宠主动行为通道 / 自发表情 / 待机动画随机切换**：本期 idle 态只跑模型默认 idle motion（库内置呼吸 / 待机），不引入"自发说话 / 自发表情切换" 主动行为；与 0004 §2.2 路线 A 长远扩展性相关，留后续需求。
- **碰撞 / 拖拽物理 / 跟随手感升级**：sprite drag / world position 数据流沿 17a 现状，不引入 spring / inertia / 拖拽惯性等物理效果。
- **状态机扩展态**：本期固定 4 态 `idle / thinking / speaking / error`；`sleeping` / `away` / `excited` / `celebrating` 等扩展态留后续需求（如有产品诉求再立）。
- **17b 前置 spike**：用户拍板"不立 spike，先跑着"——design 阶段直接选库 + 撞墙 fallback 路径登记在 design.md "已知风险"段；不立 PIXI v8 + Live2D 集成 spike。
- **macOS NSPanel 30fps cap 在 Live2D 形象上的感知评估**：沿 17a § Out of Scope + ADR 0004 §4.2 trade-off 接受；如产品验证阶段实测感知不可接受，立专项 spike（沿 0004 §6 反转条件 5）。
- **打包 / 签名 / 自动更新 / Mac App Store / 模型资源签名**：沿 ADR 0004 §4.1 + 0002 §4 暂缓清单。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；具体库选型、模型文件 ship 策略、状态机 transition table、`MouthDriver` 接口签名、push event 错误识别策略等由 [`design.md`](./design.md) 决定。

### 4.1 Live2D 模型加载与渲染

- **R-4.1.1 库选型在 design 阶段决定**：选 PIXI v8 兼容的 Live2D 库（候选 `pixi-live2d-display` v8 兼容 fork / `easy-live2d` / 其他）；design.md 须登记**选型理由 + 撞墙 fallback 路径**（如 A 库不通则切 B 库的可见回退路径）；本期不立 spike。
- **R-4.1.2 模型资源 ship**：选一个内置 Cubism 4 模型（具体模型 + 来源 + license 由 design 决定）；`.model3.json` 多文件资源（`.moc3` / `.physics3.json` / `.cdi3.json` / 贴图 / motion / expression / 物理）通过 Vite asset pipeline 在前端构建期打包；运行期模型从打包后路径加载，不走 HTTP 远程拉取。
- **R-4.1.3 Cubism Core JS 挂载**：Cubism SDK Core JS（`live2dcubismcore.min.js`）按 design 决定的挂载方式接入（CDN / 本地 / pre-bundle）；模型加载前 core JS 已可用。
- **R-4.1.4 模型在 avatar-slot 内显示**：`Live2DModel.from(modelJson)` 加载完成后 `slot.removeChildren()` + `slot.addChild(model)`（17a design §7 接缝点 #1）；外层 Container 结构 + slot 位置 / 缩放 / pointer event plumbing 全部不动。
- **R-4.1.5 模型几何与 17a 占位视觉等价**：模型在 slot 内的锚点 / 显示位置 / 占用面积与 17a 占位 `Graphics(circle) + Text("占位形象")` 的几何中心一致；drag sprite 时模型跟随 slot；bubble 跟随 anchor / 操作栏 anchor 完全不需要调整。
- **R-4.1.6 默认 idle motion 播放**：模型加载完成后自动播放库内置 idle motion / 呼吸 / 待机动作（具体 motion 名 + fallback 由 design 决定）；用户进入桌面后看到"活的"形象，不是静止贴图。

### 4.2 桌宠状态机最小集

- **R-4.2.1 4 态最小集**：状态机包含 `idle` / `thinking` / `speaking` / `error` 4 态；初始态 = `idle`；本期不引入扩展态。
- **R-4.2.2 store 与 015 对称**：新建 `frontend/src/stores/petState.ts` 含 `usePetStateStore`（phase + transitions）+ `petStatePolicy`（envelope → 状态决策）；与 015 `petBubble.ts` 文件结构 + 命名 + 测试组织对称。
- **R-4.2.3 状态切换派发到 Live2DModel**：state 进入 thinking / speaking / error 时派发到 Live2DModel motion / expression（具体派发表 由 design 决定 + motion 缺失时 fallback 由 design 决定，避免动画文件缺失导致前端崩）。
- **R-4.2.4 状态机与 015 完全正交**：`usePetStateStore` 与 `usePetBubbleStore` 各自独立 + 互不订阅 + 互不写对方；agent thinking 时气泡可以 idle，agent done 后气泡可以仍 showing（沿 015 "常驻"消失策略）。

### 4.3 Codex 兼容 · push event → 状态机驱动

- **R-4.3.1 独立 push 订阅器**：新建 `startPetStateSubscriber()` 独立 `listen("agent://push", ...)`（不分叉 015 现有 subscriber）；订阅同源 envelope、独立解析 events 流 → 状态切换；15 任何代码 / store / subscriber 不动。
- **R-4.3.2 默认 event → state 映射**：`tool_call_request` / `tool_call_result` → `thinking`；`text_delta` → `speaking`；`done` → `idle`；具体 transition table 由 design 决定（含 envelope 多 event 同帧时优先级 / `heartbeat` 是否影响态）。
- **R-4.3.3 error 态识别**：SSE 连接失败 / push 通道断连 / envelope 解析失败时进 `error` 态；具体识别策略由 design 决定；error 态恢复路径（自动 / 手动重置）由 design 决定。
- **R-4.3.4 policy 可替换扩展点**：`petStatePolicy` 沿 015 同款"module-level 变量 + `setPolicy(p)` 注入"模式（测试钩子 + 未来产品策略迭代位）。
- **R-4.3.5 015 模块代码改动最小化**：015 store (`usePetBubbleStore`) / `petBubblePolicy` / `attachBubbleWindowSync` / `startPetBubbleSubscriber` / sessionProjection / 测试 / 全 9 AC 不动；**Rust 端 `push_subscriber.rs` emit target 多发一份给 pet 窗**（line 83 `emit_to("bubble", ...)` 之后追加 `emit_to("pet", ...)` 叠加不替换；详见 [design.md §3.1 / §4.1](./design.md#31-push-event-多窗分发--rust-emit_to-叠加)）；bubble 窗收到的 envelope 与本期前完全一致。

### 4.4 lip-sync · 文本 cadence 驱动 + voice 通道扩展位

- **R-4.4.1 `MouthDriver` 接口抽象**：抽象一层 `MouthDriver` 接口（具体签名由 design 决定，输出至少包含 `mouthOpenY: 0..1`）；driver 通过 attach / detach 钩子挂到 Live2DModel 参数；具体钩子形态由 design 决定。
- **R-4.4.2 `TextCadenceMouthDriver` 实现**：本期落 driver 实现：按 `text_delta` 到达节奏 / token 长度估算时长打开 / 关闭嘴部（具体节奏算法由 design 决定，最简可走"text_delta 到达 → 开嘴 N ms → 关嘴"）；speaking 态 enter 时 attach、exit 时 detach。
- **R-4.4.3 Voice 通道扩展位**：driver 接口 + attach / detach 钩子 + speaking 态生命周期边界**为未来接入 voice_bridge audio 流预留**——未来新增 `AudioRmsMouthDriver`（订 voice_bridge audio out / 火山 RTC 客户端 PCM 流 / 滑窗 RMS → mouthOpenY）时不动状态机 / Live2DModel / 调用方。
- **R-4.4.4 不接 voice_bridge**：本期 lip-sync 不引入 voice_bridge HTTP 调用 / 不读取 audio 流（沿 [§3 Out of Scope](#3-非目标out-of-scope)）。

### 4.5 17a 接缝点 #2 / #3 / #5 零退化

- **R-4.5.1 sprite world position 数据流不动**：17a `update_sprite_pos` invoke / `emitSpritePos` hook / `BubbleState.sprite_pos` / `run_follow_loop` 跟随源全部不动；slot.x / slot.y 由 17b 状态机 / motion 系统更新时仍走 `emitSpritePos` 把世界坐标上报给 Rust（17a 既有 plumbing 不动）。
- **R-4.5.2 cursor alpha hit-test target 不动**：`usePetPassthrough` 60Hz Rust cursor channel + alpha 采样 / bounds 兜底沿 17a 现状；alpha 采样目标仍是 avatar-slot 区域；Live2D 内部 alpha 透明区由 Live2D SDK 自身渲染 + 与 slot bounds 重合区域被 alpha hit-test 自动覆盖（无需额外 plumbing）。
- **R-4.5.3 操作栏 hover bridge 不动**：slot `pointerover/out` → React `setHoverActionBar` 沿 17a；`ActionBar` 组件 / sticky 防抖 / 翻转逻辑全部不动。

### 4.6 17a Win 真机 AC 补回归

- **R-4.6.1 17a 12 条 AC Win 真机跑通**：17a AC-1 ~ AC-12 在 Win 真机跑一遍（pet 主窗整屏 transparent overlay / PIXI canvas 稳态 / 占位 → 真模型几何位置等价 / sprite drag + world position / bubble 跟随 / 操作栏 hover gate / F11 fullscreen / 015 全 AC / 016 全 AC / chat / pet 零退化 / cross-build / issue 007 008 关闭）作为 17b 进度依赖项。
- **R-4.6.2 17a 已通过的 macOS AC 不重跑**：17a macOS 7/12 已通过的 AC（AC-1 / AC-3 / AC-4 / AC-5 / AC-6 / AC-7 / AC-11）不重复跑；17a M17.9 跳过的 AC-2 / AC-8 / AC-9 / AC-10 在 17b 完成后**间接由 17b 新 AC 覆盖**（17b 状态机驱动跑通即说明 015 push 通道 + 016 bubble 通道 + chat / pet 零退化都未破坏）。

### 4.7 跨平台覆盖

- **R-4.7.1 macOS + Win 同期 first-class**：17b 新增 AC（Live2D 加载 / 状态机切换 / push event 驱动 / lip-sync 文本 cadence 节奏）macOS + Win **同期验收**；不接受任一端 fallback。
- **R-4.7.2 cfg-gate 不需要新增**：17b 改动集中在前端 PIXI / store / driver 层，跨平台行为差异由 17a 既有 cfg-gate（macOS NSPanel + Win 整屏 alwaysOnTop）兜住；本期不引入新 `#[cfg(target_os = "...")]` gate。
- **R-4.7.3 Linux 不在范围**：沿 ADR 0002 §3.1 / ADR 0004 §5.3。

### 4.8 既有路径回归

- **R-4.8.1 015 全 9 条 AC 仍然成立**：015 AC-1 ~ AC-9（owner / 双订阅 / 事件分发 policy / 主动轮分流 / silent turn 丢弃 / sessionProjection 兼容 / 跨 Space / 既有路径零退化 / dev CLI 端到端）在本期完成后全部回归通过。
- **R-4.8.2 016 全 12 条 AC 仍然成立**：016 AC-1 ~ AC-12 在本期完成后全部回归通过。
- **R-4.8.3 17a 全 12 条 AC 仍然成立**：macOS 已通过的 7/12 不退化；Win 补回归后达 12/12（沿 R-4.6.1）。
- **R-4.8.4 既有数据通路改动最小化**：015 store / policy / `startPetBubbleSubscriber` / `attachBubbleWindowSync` / sessionProjection / 016 bubble window / 17a sprite world position 数据流 / 17a 操作栏 plumbing / 17a cursor passthrough 不动；**唯一改动**是 015 Rust 模块 `push_subscriber.rs` 的 emit target 多发一份给 pet 窗（沿 R-4.3.5 / [design.md §3.1](./design.md#31-push-event-多窗分发--rust-emit_to-叠加)）。

### 4.9 向后兼容

- **R-4.9.1 chat 窗对话流零变更**：user 在 chat 窗发起对话端到端行为完全不变。
- **R-4.9.2 既有 bubble 显隐 / size / 内容渲染零变更**：bubble window 由 015 store 状态驱动 show/hide、按文本长度调 size、`<PetBubble />` 组件 JSX 全部维持。
- **R-4.9.3 bridge / engine / 015 / 016 / 17a 任何接口零改动**。
- **R-4.9.4 sessionProjection 既有投影行为零变更**。

---

## 5. 使用约束

- **沿用 0004 锁定的实现路径骨架**：本期不重复 0004 §2.1 决策清单 / §5.1 macOS 路径 / §5.2 Win 路径细节；如发现 0004 锁定的某条与本期落地冲突，应回头评估 0004 是否需要新建编号更大的 ADR 覆盖（沿 docs-discipline 铁律 1），而不是把变更塞进本需求。
- **沿用 17a design §7 接缝点表**：本期 design 阶段直接 cherry-pick 17a 接缝点 #1 + #4 到 main 仓相应位置；不重新论证接缝点划分。
- **不立 17b 前置 spike**：用户拍板"先跑着"——design 阶段直接选库 + 撞墙 fallback 路径登记；如实施期撞墙不可绕，回头评估是否补 spike。
- **跨平台开发脚本约定**：本期如新增"非写代码"开发操作（含新 dev 启动入口、调试脚本），按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落地（`run.sh` + `run.ps1`），并在 `scripts/README.md` 登记。
- **frontend-ui-conventions 约束**：本期新增 UI 配置 / store / driver 模块遵循 [`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc)。
- **真实 LLM 调用授权**：本期 dev CLI 端到端回归验证会经由 015 / 014 触发 LLM 调用，跑前需获用户授权；本期前端**不引入额外维度的 LLM 调用**。
- **新增依赖最小化**：仅引入选定的 Live2D 库 + Cubism SDK Core JS（具体由 design 决定）；其余依赖不引。

---

## 6. 验收标准

> 本节 AC 是**机制层面 + 形象呈现层面的过程性标准**——验证"Live2D 形象正确加载 / 状态机正确响应 push event / lip-sync 文本 cadence 触发 / 17a 接缝点不退化 / 既有路径不退化"，**不含"形象动得漂亮 / 嘴型对得严丝合缝 / 状态切换帧间过渡丝滑"等产品层判断**（那些属于 17c / 产品验证阶段）。

- **AC-1 Live2D 模型加载**：桌面启动后 avatar-slot 内 Live2D 模型成功加载（Cubism Core JS + model3.json 多文件 + idle motion 内置播放）；macOS + Win 双端通过；模型几何位置 / 锚点 / 占用面积与 17a 占位视觉**几何等价**（drag sprite / bubble 跟随 / 操作栏 anchor 不需调整）。
- **AC-2 默认 idle motion 播放**：模型加载完成后自动播放库内置 idle / 呼吸 / 待机 motion；用户看到的是"活的"形象，不是静止贴图。
- **AC-3 状态机 4 态切换**：触发 dev CLI 跑端到端 → push envelope 流入 → 桌宠状态机在 idle / thinking / speaking / done 之间正确切换（具体观测方式：Live2DModel motion / expression 变更 + `usePetStateStore` phase 变更 log）；error 态在 SSE 断连时可观测进入。
- **AC-4 push event → state 派发正确**：`tool_call_request` / `tool_call_result` 触发进 thinking 态、`text_delta` 触发进 speaking 态、`done` 触发回 idle 态（具体 transition table 由 design 决定但 AC 验"端到端事件流过且态正确切换"）。
- **AC-5 lip-sync 文本 cadence 节奏**：speaking 态下 `TextCadenceMouthDriver` attach 成功 + 嘴部参数（`mouthOpenY` 或 Live2D 等效参数）按 `text_delta` 到达节奏开闭；speaking 态 exit 时 driver detach + 嘴部回零。
- **AC-6 `MouthDriver` 接口扩展位**：driver 接口 + attach / detach 钩子在代码层可见（具体可读 code review / hello-world `AudioRmsMouthDriver` stub 验证；不要求本期真接 voice）。
- **AC-7 17a 接缝点 #2 / #3 / #5 零退化**：drag sprite + bubble 跟随 / cursor alpha hit-test 触发 / 操作栏 hover gate 在 17b 完成后完全等价 17a 已通过的行为（具体跑 17a AC-4 / AC-5 / AC-6 子集等价回归）。
- **AC-8 17a 12 条 AC Win 真机跑通**：17a AC-1 ~ AC-12 在 Win 真机跑一遍 12/12 通过（pet 整屏 / PIXI canvas / 模型加载等价占位 / sprite drag / bubble 跟随 / 操作栏 hover gate / F11 fullscreen / 015 / 016 / chat / pet 零退化 / cross-build / issue 关闭）。
- **AC-9 015 全 9 条 AC 回归通过**：015 AC-1 ~ AC-9 在本期完成后全部跑通。
- **AC-10 016 全 12 条 AC 回归通过**：016 AC-1 ~ AC-12 在本期完成后全部跑通。
- **AC-11 既有 chat / pet 行为零退化**：user 在 chat 窗输入对话全流程行为不变；pet 形象左键拖拽 / 透明区穿透 / 托盘菜单 / 操作栏点击"打开对话" 行为不变。
- **AC-12 一份代码 + cross-build 全绿**：17b 不引入新 cfg-gate；`./scripts/check`（lint + typecheck + 单测）三平台全绿；`cargo build` macOS / Win 都通；`pnpm test` 全过（含新增 `petState` / `MouthDriver` 单测 + 既有 015 / 016 / 17a 单测全部维持）。

---

## 7. 已知风险与监测项（不阻塞验收 / 不进 AC）

本节登记本期"已接受 / 监测中 / 不阻塞"的风险，供 design 阶段 + 实施期参考；任一项升级为"不可绕阻塞"时回头评估是否补 spike / 立新 ADR / 拆 17c。

| # | 风险 / 监测项 | 来源 | 处理 |
|---|---|---|---|
| 1 | PIXI v8 + 选定 Live2D 库的兼容性 | 17b 不立前置 spike（用户拍板）+ `pixi-live2d-display` 历史只兼容 v6/v7 | design 选型时登记 fallback 路径（A 库不通切 B 库）；实施期撞墙现场切；不可绕时回头评估补 spike |
| 2 | Cubism Core JS 挂载方式（CDN / 本地 / pre-bundle） | Live2D SDK 历史采用 license 限制 + script tag 挂载 | design 决定挂载方式 + 备份方案；不在 AC 卡 |
| 3 | 模型 `.model3.json` 多文件资源 Vite asset pipeline 处理 | Vite 默认 asset handling 对多文件资源 + 动态加载 path resolution 有边界 | design 决定 ship 策略（public/ 拷贝 / asset import / 路径转换）；实施期撞墙微调 |
| 4 | lip-sync 文本 cadence 真感官（嘴动与"说话"是否对得上）| 本期不深入 + 留 voice 接入扩展位 | 不在 AC 卡严丝合缝；产品验证阶段如感知"假"明显，立 17c 接 voice mel/RMS |
| 5 | Live2D motion / expression 文件缺失导致前端崩 | 模型 ship 时可能漏文件 | design 决定 motion 缺失 fallback（log warn + 不切动作）；不在 AC 卡 |
| 6 | macOS NSPanel 30fps cap 在 Live2D 形象上感知评估 | ADR 0004 §4.2 + 17a § Out of Scope | 沿用接受；产品验证阶段如不可接受立 0004 §6 反转条件 5 专项 spike |
| 7 | Win 端 24h 长跑稳定性 + Live2D 模型长期内存表现 | 17a Win spike "未补的数据" + Live2D 模型本身长期持有 GPU 资源 | 不作为 17b 硬门槛；产品稳定后做一次 24h 长跑复测 |
| 8 | error 态触发频度（SSE 断连 / push 通道异常） + 错误恢复路径 | push 通道当前没有详细 error 模型 | design 决定 error 识别策略 + 恢复路径；实施期监测 error 态触发频度调整 |

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-15 | M18.1 探查触发 design §3.3 fallback：公开 npm 上不存在 PIXI v8 兼容的 `pixi-live2d-display` fork（主仓 v6 / 3 个主流社区 fork 全 v7），用户拍板切 `easy-live2d@^0.4.4`。需求 §1.2 / §2 / §4.1.1 / §5 / §7 中"主选 `pixi-live2d-display` fork、撞墙 fallback 切 `easy-live2d`"措辞口径调整为"pin `easy-live2d`，进一步 fallback 是 Cubism Web Samples 自渲染 / 降 PIXI 到 v7"；不动需求范围 / AC / 风险表语义，仅库名 pin 与 fallback 排序更新。详见 [design.md §3.3 / §13](./design.md#33-live2d-库选型-pin-easy-live2d044)。 | §1.2 / §4.1.1 / §5（库名 pin 措辞） |
| 2026-06-15 | design 阶段澄清 015 模块代码改动最小面：R-4.3.5 / R-4.8.4 措辞由"15 完全不动"放宽为"15 store / policy / subscriber 启动方式 / sessionProjection / 测试 / 全 9 AC 不动；唯一改动是 Rust `push_subscriber.rs` emit target 多发一份给 pet 窗（叠加不替换）"。理由：push 通道当前只 `emit_to("bubble", ...)`，pet 窗收不到 envelope；17b 状态机驱动必须让 pet 窗收 push event。详见 [design.md §3.1 / §4.1](./design.md#31-push-event-多窗分发--rust-emit_to-叠加)。 | R-4.3.5 / R-4.8.4 |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-15
- **确认时间**：2026-06-15
- **关联决策**：[ADR 0004 · 桌宠承载形态与渲染路径](../../decisions/0004-pet-overlay-route/README.md)
- **关联需求**：
  - [需求 014](../014-engine-main-loop-and-bridge-push/)（push 通道源头）
  - [需求 015](../015-desktop-pet-bubble-and-conversation-owner/)（push 通道桌面端消费 · `petBubblePolicy` 对称参考）
  - [需求 016](../016-pet-bubble-independent-window/)（bubble window 机制层）
  - [需求 017 / 17a](../017-pet-overlay-form-switch/)（avatar-slot Container 底座 + design §7 5 接缝点表）
  - [需求 007](../007-voice-call/)（voice_bridge 未来接入 · lip-sync `MouthDriver` 扩展位）
- **下期承接**：17c 候选——audio mel/RMS lip-sync（接 007 voice_bridge audio 流）/ 多模型切换 / 主动行为通道
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
