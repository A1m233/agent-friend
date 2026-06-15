# 017 · 桌宠承载形态切换为整屏 transparent overlay（pet overlay form switch · 17a 形态切换底座）

> Pet Overlay Form Switch — 17a Form Base
>
> 把桌宠承载形态从 016 落地的"240×320 固定 pet 主窗"切换为 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) 锁定的"**整屏 transparent overlay**"路线（macOS = NSPanel + nonactivating；Win = 普通窗 + alwaysOnTop）。本期是 0004 路线 A 的工程落地第一步——**只交付形态切换底座**（PIXI canvas + 可替换形象容器 + sprite world position 数据流 + 操作栏 sprite-relative 浮动 UI 层），不上 Live2D / 不加状态机；那些是下期需求 017b 在本期底座上做的事。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已完成（Completed）

---

## 1. 背景

### 1.1 现状

- [需求 010](../010-desktop-shell-and-chat-ui/) 落地 240×320 固定 pet 主窗 + 占位形象。
- [需求 015](../015-desktop-pet-bubble-and-conversation-owner/) 落地桌宠气泡机制（push 通道 / owner / policy / store / PetBubble），**9 条 AC 全部通过**。
- [需求 016](../016-pet-bubble-independent-window/) 落地独立 bubble window + 16ms tick 跟随 pet 主窗 outer_position，**12 条 AC 全部通过**。
- [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) 锁定项目级路线 A · 整屏 transparent overlay + macOS NSPanel + nonactivating + PixiJS / WebGL 路径骨架。
- [`spike-tauri-nspanel-pet-window.md`](../../explorations/desktop-pet-form-factor/spike-tauri-nspanel-pet-window.md) §5.5 提供 macOS 端完整可借鉴代码 listing（panel 转换 / setup hook 整屏 / collection behavior / activation policy），状态 done(pass-with-caveats)。
- [`spike-tauri-win-overlay-research.md`](../../explorations/desktop-pet-form-factor/spike-tauri-win-overlay-research.md) §6.4.4 提供 Win 端完整可借鉴代码 listing（`apply_pet_overlay_fullscreen` cross-platform fn / PIXI v8 整屏 demo / `setIgnoreCursorEvents` 时机 / StrictMode 双 mount cleanup pattern），真机段 done(pass)：fps ≈ 300（远超 macOS 30fps cap）、GDI_total 5 分钟稳定 0/秒、跨双屏 DPR (175%/125%) setBounds 正常、**F11 fullscreen 浮层 Win 平台 alwaysOnTop 天然支持**。

### 1.2 这次要做什么

按 ADR 0004 §2.1 决策清单 + 两份 spike 的可借鉴代码 listing，把桌宠承载形态从"小固定窗"切换到"整屏 transparent overlay"，并把后续叠加形象本体（Live2D）所需的底座一次落地：

- **pet 主窗物理形态切换**：240×320 固定窗 → 整屏 monitors union（macOS 走 NSPanel + nonactivating + fullScreenAuxiliary；Win 走普通窗 + alwaysOnTop + skipTaskbar；两端透明 + 无装饰 + 无阴影沿用 016 已配）。
- **PIXI canvas + 可替换形象容器底座**：在整屏 webview 内挂 PIXI v8 应用，canvas resizeTo viewport；形象用 **PIXI Container（avatar slot）** 承载，内部 children 由占位内容（Graphics + Text）填充等价重现 016 当前视觉；不引入新资源。
- **sprite world position 数据流**：sprite 在 PIXI stage 内的拖拽 / 移动产生的 world position 上报给 Rust，作为下游 bubble 跟随源 + 后续 17b 状态机的统一坐标基准。
- **bubble 跟随源切换**：016 的 16ms tick 由 read-pet-outer-position（整屏后永远 (0,0)，不可用）替换为 read-sprite-world-position；**push 通道 / owner / policy / bubble window 机制完全不动**，仅替换数据源。
- **操作栏 sprite-relative 浮动 UI 层**：016 在 pet 窗内 flex 居中的"打开对话"按钮 / dev 按钮（💬 / 📜）改为以 sprite 为锚的相对浮动布局，并补 hover gate（顺手解 [issue 008](../../issues/008-pet-action-bar-hover-gate/)）。
- **跨平台 first-class 同期交付**：macOS + Win 同一期落，沿两份 spike 的 cfg-gate 两端各自 plumbing 模式（macOS NSPanel 一段 / Win `apply_pet_overlay_fullscreen` 一段）。
- **不上 Live2D / 不加状态机 / 不做 lip-sync / 不做 Codex 兼容**：那些是 017b 在本期底座上做的事，本期 In Scope 不含。

### 1.3 与 015 / 016 的关系

015 是机制层 Tier 0 封口（**信息从哪儿来、按什么规则到哪儿去**），016 是 015 之后承载层第一步（**气泡装在独立 window 里**）。017a 是 015/016 之后承载形态的第二步（**形象本身从"窗里"切到"屏上"**）。

机制层：015 push 通道 / owner / policy / store **完全不动**；016 bubble window + show/hide/size 控制 **完全不动**；唯一动的是 016 R-4.4 的"位置跟随" 数据源——由 read-pet-outer-position 替换为 read-sprite-world-position，**语义对用户等价**（拖形象 / 气泡跟随 / 跨屏 / 翻转），实现源替换不修订 016 文档。

015 全 9 条 AC + 016 全 12 条 AC 在本期完成后必须**全部回归通过**。

### 1.4 与 ADR 0004 的关系

0004 是项目级路线决策，**只决方向 + 实现路径骨架**；017a 是 0004 的工程落地第一步——把 §2.1 决策清单（NSPanel / nonactivating / Floating / fullScreenAuxiliary / Accessory / monitors union / PixiJS+WebGL / 60Hz cursor passthrough / Win alwaysOnTop 路径）从两份 spike 的"可借鉴代码 listing"cherry-pick 到 main 仓的产品代码位置。

本期**不重复**0004 已锁定的技术栈选型与平台路径（沿 docs-discipline 铁律 3），需求文档只讲"做什么 / 做到什么程度算成功"。

### 1.5 与下期 017b 的关系

017b（下期需求）在本期底座上做：

- 形象内容替换：占位 PIXI Sprite → Live2DModel
- 桌宠状态机：idle / thinking / speaking / error 等态切换
- Codex 兼容：与现有 engine event 流对齐
- lip-sync：speaking 态下口型同步

接缝清晰、互不交错：**17a 提供容器 + 数据流 + UI 容器；17b 替形象内容 + 加状态机驱动**。本期 In Scope 不含 17b 任一项；本期 design 阶段会沉淀具体接缝点（占位 Sprite 替 Live2DModel 在哪 / 状态机 hook 点 / lip-sync 接口形态 / Codex 事件接入点等），17b 立项时直接接力。

### 1.6 跨平台定位

agent-friend 跨平台桌面应用，**Windows = first-class 平台、不是 fallback**（沿 [ADR 0002](../../decisions/0002-incubation-tech-stack/README.md) §3.1）。

ADR 0004 §4.4 当初接受的"Win 整屏 overlay + WebGL 未真验"trade-off，已由 Win spike 真机段（2026-06-15）**实证可工程化**：

- PixiJS rAF 稳态 ≈ 300fps（远超 §3 通过条件 ≥ 55fps 阈值；完全无 macOS NSPanel + WKWebView 30fps cap）
- GDI_total 5 分钟稳定（wry#1691 在用户环境实测不复现）
- 跨双屏不同 DPR setBounds 正常工作
- **F11 fullscreen 浮层天然支持**（Win alwaysOnTop + 整屏 transparent 不需要 NSPanel + nonactivating + fullScreenAuxiliary 加料；对照 macOS [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) 是 Win 平台的结构性优势）

本期 macOS + Win 端到端 **AC 同期验证**；两端形态语义一致（"形象在桌面上活着"），不接受 Win 走小固定窗 fallback。Linux 沿 0002 §3.1 不在范围。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **Pet 主窗形态切换** | pet 主窗物理尺寸由 016 落地的 240×320 切到 monitors union 整屏；macOS 走 NSPanel + nonactivating + fullScreenAuxiliary + Accessory activation policy；Win 走普通窗 + alwaysOnTop + skipTaskbar；两端 transparent + 无装饰 + 无阴影沿用 016 |
| **PIXI canvas 底座** | 整屏 webview 内挂 PIXI v8 应用；canvas resizeTo viewport + autoDensity + 跨 DPR；StrictMode 双 mount 用 `cancelled` flag + `pixi.destroy(true, { children: true, texture: true })` 防泄漏（沿两份 spike §5.5.7 / §6.4.4.7 同款 cleanup pattern） |
| **可替换形象容器** | 形象用 **PIXI Container（avatar slot）** 承载，内部 children 由占位内容（Graphics + Text）等价重现 016 当前 pet 占位视觉；不引入新资源；**外层 Container 结构**为下期 17b 内部 children 替换为 Live2DModel 预留（Live2DModel 与 Sprite 都是 Container 的兄弟子类；用 Container 作 slot 让 17a/17b 5 个接缝点中 4 个 plumbing 挂外层、17b 不重接） |
| **sprite 拖拽 + world position 数据流** | sprite 在 PIXI stage 内可拖拽；sprite world position 经 Tauri invoke / event 上报给 Rust 作为统一坐标基准；具体频率 / 通道形态由 design 决定 |
| **bubble 跟随源替换** | 016 的 16ms tick 由 read-pet-outer-position 替换为 read-sprite-world-position；016 R-4.4.1 ~ R-4.4.4 的跟随手感（跟随精度 / 屏顶翻转 / 跨平台机制等价）全部维持 |
| **操作栏 sprite-relative 浮动 UI 层** | 016 在 pet 窗内 flex 居中的"打开对话"按钮 / dev 按钮（💬 / 📜）改为以 sprite 为锚的相对浮动布局（具体 PIXI Container vs DOM absolute 由 design 决定）；补 hover gate（鼠标进入形象 + 操作栏整体 group 才显示） |
| **cursor passthrough** | 整屏 transparent overlay 形态下，前端 mount 时调用 `setIgnoreCursorEvents(true)` 让整屏 webview 不拦截鼠标；形象 / 操作栏命中区由 Rust 60Hz cursor polling + alpha 采样 hit-test toggle 承担（沿 [`spike-alpha-hittest-perf.md`](../../explorations/desktop-pet-form-factor/spike-alpha-hittest-perf.md) plumbing 思路；具体由 design 决定） |
| **跨平台两端 plumbing** | macOS NSPanel 路径沿 macOS spike §5.5 listing；Win 整屏 + alwaysOnTop 路径沿 Win spike §6.4.4 listing；setup hook 两端各自 `#[cfg(target_os = "...")]` gate，cross-platform `apply_pet_overlay_fullscreen` 函数共用 |
| **既有路径回归** | 015 全 9 条 AC + 016 全 12 条 AC 在本期完成后全部回归通过 |
| **副产品 issue 关闭** | [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) macOS NSPanel + fullScreenAuxiliary 自然解；[issue 008](../../issues/008-pet-action-bar-hover-gate/) 操作栏改造时一并补 hover gate |

---

## 3. 非目标（Out of Scope）

以下本期**明确不做**，避免范围蔓延：

- **Live2D 接入**：占位形象用 PIXI Sprite + 016 现有贴图；Live2DModel + 库选型（pixi-live2d-display vs easy-live2d）+ 模型加载策略全部留给 017b。
- **桌宠状态机**：idle / thinking / speaking / error 等态切换 + 与 015 push 事件流对齐留给 017b。
- **Codex 兼容 / lip-sync**：与 17b 状态机 + Live2D 接入耦合，本期不动。
- **bubble window / 015 push 通道 / owner / policy 任何改动**：本期只动"形象本身从窗里切到屏上"这一层 + bubble 跟随源数据源替换。
- **新增形象资源**：占位贴图直接复用 016 现有 pet 占位形象。
- **多 DPR 多屏 sprite world position vs CSS px 坐标系映射深查**：Win spike §6.4.3 / §6.4.4.9 takeaway 5 已转交"真做 Live2D 接入需求 (=017b)"的 design 阶段；本期不深查（不影响 17a 整屏 setBounds 功能性验证）。
- **桌宠主动行为通道 / 拖拽持久化 / 多 pet 同时出现 / 形象跨屏漫游策略**：与 0004 §2.2 路线 A 长远扩展性相关，本期不做；留待后续需求。
- **macOS NSPanel 路径反向求证（如尝试 60fps cap 绕过）**：沿 ADR 0004 §4.2 trade-off 接受 30fps；如未来产品验证阶段实测感知不可接受，单独立 spike（沿 0004 §6 反转条件 5）。
- **打包 / 签名 / 自动更新 / Mac App Store**：沿 ADR 0004 §4.1 + 0002 §4 暂缓清单，Phase 1 启动前再立。
- **17a 独立 spike**：macOS spike §5.5 + Win spike §6.4.4 已是本期的物理底座 ground truth；本期 design 阶段直接 cherry-pick，不再立 17a 前置 spike。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；具体接口形态、PIXI 整合细节、cfg-gate 两端代码组织、sprite world position 上报频率 / 通道、操作栏定位实现选型等由 [`design.md`](./design.md) 决定。

### 4.1 Pet 主窗形态切换

- **R-4.1.1 物理尺寸切到 monitors union 整屏**：pet 主窗物理 width / height = 全部显示器的 union；多屏拼接（跨 DPR）正确覆盖；启动后无可见窗边界 / 无装饰 / 无阴影 / 不进任务栏。
- **R-4.1.2 macOS 走 NSPanel 路径**：window 实际类型 = NSPanel（沿 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §5.1 决策清单），style mask = nonactivating，level = Floating，collection behavior = fullScreenAuxiliary + canJoinAllSpaces，activation policy = Accessory。
- **R-4.1.3 Win 走普通窗 + alwaysOnTop 路径**：window 普通 Window（无 NSPanel 等价），透明区由 tao 在 `set_ignore_cursor_events(true)` 时自动加 `WS_EX_TRANSPARENT | WS_EX_LAYERED`（沿 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §5.2）。
- **R-4.1.4 整屏 transparent overlay 不抢焦点**：pet 窗 show 时不抢键盘焦点 / 不进 Cmd+Tab / Dock（macOS）/ 任务栏（Win）；用户在 chat 窗 / 其他 app 输入态时形象浮现不打断。

### 4.2 PIXI canvas + 可替换形象容器底座

- **R-4.2.1 PIXI 应用挂载**：在整屏 webview root 内挂 PIXI v8 Application；canvas resizeTo viewport + autoDensity + resolution = devicePixelRatio + backgroundAlpha = 0。
- **R-4.2.2 形象容器**：形象用 **PIXI Container（avatar slot）** 承载，内部 children 由占位内容填充；**外层 Container 结构**为下期 17b 内部 children 替换为 Live2DModel 预留——Live2DModel 与 PIXI.Sprite 都是 PIXI.Container 的兄弟子类，用 Container 作 slot 让 17a/17b 5 个接缝点中 4 个 plumbing（sprite world position 数据流 / cursor hit-test target / 状态机 hook 点 / 操作栏 hover bridge）挂在外层 Container 上、17b 不重接（接缝点细则由 design 沉淀）。
- **R-4.2.3 占位内容等价重现 016 视觉**：占位内容在 PIXI Container 内**等价重现 016 现有 pet 占位视觉**（圆形 bg-accent + "占位形象" 4 字）；具体 PIXI 类型（Graphics + Text 组合 / 后续也可换成 Sprite + 占位 PNG，但本期不引入新资源）由 design 决定；用户感官上"图没换、窗框消失"。
- **R-4.2.4 StrictMode 双 mount 不泄漏**：React 19 + StrictMode dev 下 PIXI 异步初始化 + cleanup 顺序正确（`cancelled` flag + `pixi.destroy(true, { children: true, texture: true })`）；dev 反复刷新不积累 PIXI app 实例 / 不丢 GPU context。
- **R-4.2.5 跨 DPR 渲染正确**：跨双屏不同 DPR（如 175% + 125%）下 PIXI canvas 渲染清晰、无模糊、无错位。

### 4.3 sprite 拖拽 + world position 数据流

- **R-4.3.1 sprite 拖拽**：sprite 在 PIXI stage 内可拖拽；拖拽手感连贯（无明显延迟 / 跳跃）。
- **R-4.3.2 world position 上报**：sprite 移动时其 world position（PIXI stage 坐标系）上报给 Rust，作为下游 bubble 跟随源 + 后续 17b 状态机 / 主动行为通道的统一坐标基准；上报频率 / 通道形态（event 还是 invoke / 节流策略）由 design 决定。
- **R-4.3.3 拖拽与 cursor passthrough 协同**：sprite 命中区由 alpha hit-test 决定（沿 [`spike-alpha-hittest-perf.md`](../../explorations/desktop-pet-form-factor/spike-alpha-hittest-perf.md) plumbing）；透明区点击穿透到下方 app。

### 4.4 Bubble 跟随源切换

- **R-4.4.1 跟随源由 outer_position 替换为 sprite world position**：016 R-4.4 的"位置跟随"机制层语义维持，唯一替换数据源：16ms tick 不再 read pet 窗 outer_position（整屏后永远 (0,0)，不可用），改为 read sprite world position（经 PIXI stage → screen coord 映射 → bubble window setPosition）。
- **R-4.4.2 跟随手感对用户等价**：016 R-4.4.1 ~ R-4.4.4 的跟随手感全部维持——拖 sprite → bubble 同步移动 / 跟随精度无可感知错位 / 屏顶贴墙翻转 / 跨平台机制等价。
- **R-4.4.3 016 文档不修订**：016 R-4.4.x 既有措辞"跟随 pet 主窗 outer_position"不动；本期把"实现源替换"作为 017a 范围内的 plumbing 改动落地，与 016 用户体感等价。

### 4.5 操作栏 sprite-relative 浮动 UI 层

- **R-4.5.1 改为 sprite-relative 浮动**：016 在 pet 窗内 flex 居中的"打开对话"按钮 / dev 按钮（💬 / 📜）改为以 sprite 位置为锚的相对浮动布局；pet 窗变整屏后，按钮不再"整屏中心"漂着。
- **R-4.5.2 实现选型留 design**：sprite-relative 浮动 UI 用 PIXI Container 还是 DOM absolute 由 design 决定（沿 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §8 表格"PIXI Container 或 DOM absolute"留两种）。
- **R-4.5.3 补 hover gate**：操作栏默认隐藏；鼠标进入 sprite + 操作栏整体 group 才显示；mouse leave 渐隐（语义同 [issue 008](../../issues/008-pet-action-bar-hover-gate/) 倾向的"最小补丁"方向）。
- **R-4.5.4 dev 按钮显示策略**：dev-only 注入按钮（💬 / 📜）沿用 016 `import.meta.env.DEV` gate，但显示策略与 R-4.5.3 hover gate 一致（不破坏"安静悬浮"语义）。

### 4.6 Cursor passthrough

- **R-4.6.1 整屏 mount 时显式 setIgnoreCursorEvents(true)**：前端 useEffect mount 时调一次 `getCurrentWindow().setIgnoreCursorEvents(true)`，让整屏 webview 不拦截鼠标（沿 Win spike §6.4.4.9 takeaway 3 实测教训）。
- **R-4.6.2 形象 / 操作栏命中区由 hit-test toggle 承担**：Rust 60Hz cursor polling + alpha 采样 / DOM hit-test 决定何时 toggle off `setIgnoreCursorEvents`；具体 plumbing 沿 [`spike-alpha-hittest-perf.md`](../../explorations/desktop-pet-form-factor/spike-alpha-hittest-perf.md) 由 design 决定。

### 4.7 既有路径回归

- **R-4.7.1 015 全 9 条 AC 仍然成立**：015 AC-1 ~ AC-9（owner / 双订阅 / 事件分发 policy / 主动轮分流 / silent turn 丢弃 / sessionProjection 兼容 / 跨 Space / 既有路径零退化 / dev CLI 端到端）在本期完成后全部回归通过。
- **R-4.7.2 016 全 12 条 AC 仍然成立**：016 AC-1 ~ AC-12（pet 主窗物理尺寸 → 整屏后语义等价更新 / 气泡走独立 window / 长文本不裁切 / 跟随 / 不抢焦点 / 跨 Space-全屏 / 透明区穿透 / 015 全 AC / 既有 chat-pet 行为零退化 / cross-build 全绿 / dev CLI / issue 005 关闭）在本期完成后全部回归通过；其中 AC-1 物理尺寸口径由"240×320 固定窗"切到"monitors union 整屏"，**用户感官等价**（窗框消失但形象 + 拖拽 + bubble 跟随手感不变），不修订 016 AC 文案。
- **R-4.7.3 既有数据通路零改动**：015 push subscriber / owner / policy / store / sessionProjection / 016 bubble window 显隐 / size 控制 / chat 窗对话流端到端**完全不动**。

### 4.8 跨平台覆盖

- **R-4.8.1 macOS + Win 同期 first-class**：macOS 端到端 + Win 端到端 AC **同期验收**；不接受任一端 fallback 到固定窗 / 留下期。
- **R-4.8.2 两端 plumbing 各自 cfg-gate**：sprite world position 上报 / bubble 跟随机制 / PIXI canvas 渲染 / 操作栏布局 / cursor passthrough 这些"上层逻辑"用一份代码；setup hook 中 NSPanel 转换（macOS）vs `apply_pet_overlay_fullscreen` 调用（Win）走各自 `#[cfg(target_os = "...")]` gate。
- **R-4.8.3 cross-platform 函数共用**：`apply_pet_overlay_fullscreen`（monitors union setBounds 算法）跨平台共用，沿 macOS spike §5.5.5 + Win spike §6.4.4.2 同款算法。
- **R-4.8.4 Linux 不在范围**：沿 ADR 0002 §3.1 / ADR 0004 §5.3。

### 4.9 副产品 issue 关闭

- **R-4.9.1 issue 007 关闭**：macOS NSPanel + fullScreenAuxiliary 自然解 macOS fullscreen Space 浮层限制（沿 ADR 0004 §1 + §5.1）；Win 平台 alwaysOnTop 实测天然支持（Win spike §6.4.3 Bonus finding）。本期完成后 issue 007 置 `resolved` + 写修复指向。
- **R-4.9.2 issue 008 关闭**：操作栏 sprite-relative 浮动 UI 层改造（R-4.5）顺手解 hover gate 漂移。本期完成后 issue 008 置 `resolved` + 写修复指向。

### 4.10 向后兼容

- **R-4.10.1 既有 chat 窗对话流零变更**：user 在 chat 窗发起对话端到端行为完全不变。
- **R-4.10.2 既有 bubble 显隐 / size / 内容渲染零变更**：bubble window 由 store 状态驱动 show/hide、按文本长度调 size、`<PetBubble />` 组件 JSX 全部维持。
- **R-4.10.3 bridge / engine / 015 / 016 任何接口零改动**。
- **R-4.10.4 sessionProjection 既有投影行为零变更**。

---

## 5. 使用约束

- **沿用 0004 锁定的实现路径骨架**：本期不重复 0004 §2.1 决策清单 / §5.1 macOS 路径 / §5.2 Win 暂定路径细节；如发现 0004 锁定的某条与本期落地冲突，应回头评估 0004 是否需要新建编号更大的 ADR 覆盖（沿 docs-discipline 铁律 1），而不是把变更塞进本需求。
- **沿用 macOS spike §5.5 + Win spike §6.4.4 listing**：本期 design 阶段直接 cherry-pick 两份 listing 到 main 仓相应位置，不再立 17a 前置 spike。
- **跨平台开发脚本约定**：本期如新增"非写代码"开发操作（含新 dev 启动入口、调试脚本），按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落地（`run.sh` + `run.ps1`），并在 `scripts/README.md` 登记。
- **frontend-ui-conventions 约束**：本期新增 UI 配置 / Tauri window 配置 / Rust 模块遵循 [`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc)。
- **真实 LLM 调用授权**：本期 dev CLI 端到端回归验证会经由 015 / 014 触发 LLM 调用，跑前需获用户授权；本期前端**不引入额外维度的 LLM 调用**。
- **新增依赖最小化**：仅引入 `pixi.js ^8.6.0`（沿 Win spike §6.4.4.6 已实装 8.19.0）；macOS 端 `tauri-nspanel` git 依赖沿 macOS spike §5.5.1 已落 Cargo.toml（如尚未合入则本期落入），其余依赖不引。

---

## 6. 验收标准

> 本节 AC 全部是**机制层面的过程性标准**——验证"承载形态切换底座能跑通、015/016 既有路径不退化、副产品 issue 关闭"，**不含任何"形象动得好不好看 / sprite 拖拽手感是否丝滑"等产品层判断**（那些归 17b 上 Live2D 之后的产品验证）。

- **AC-1 Pet 主窗整屏 transparent overlay**：macOS 上 pet 主窗实际类型 = NSPanel + style mask nonactivating + collection behavior fullScreenAuxiliary + canJoinAllSpaces；Win 上 pet 主窗 = 普通 Window + transparent + alwaysOnTop + skipTaskbar + decorations:false + shadow:false。两端启动后无可见窗边界，pet 窗物理尺寸 = monitors union（跨双屏 setBounds 正确覆盖）。
- **AC-2 PIXI canvas 整屏渲染稳态**：macOS NSPanel 上 PixiJS rAF 稳态 ≥ 30fps（沿 macOS spike §3.2 段 B 通过条件 + ADR 0004 §4.2 已接受 trade-off）；Win 上 PixiJS rAF 稳态 ≥ 55fps（沿 Win spike §3 通过条件）；30 秒稳态采样无明显掉帧。
- **AC-3 形象占位渲染等价**：形象在 PIXI Container（avatar slot）内**等价重现 016 现有 pet 占位视觉**（圆形 bg-accent + "占位形象" 4 字），视觉上等价 016 现状（图像本身无变化，仅窗框消失）。
- **AC-4 sprite 拖拽 + world position 数据流**：用户拖 sprite → sprite 在屏内可移动；sprite world position 经数据通道上报给 Rust，Rust 侧能拿到当前 world position（具体通道与节流由 design 决定，但 AC 验"端到端数据有流过")。
- **AC-5 bubble 跟随源切换 · 016 跟随手感等价**：拖 sprite → bubble window 同步跟随；016 AC-4 跟随手感（跟随精度无可感知错位）维持；屏顶贴墙翻转沿 016 R-4.4.3 行为。
- **AC-6 操作栏 sprite-relative + hover gate**：操作栏（"打开对话"按钮 + dev 💬 / 📜）以 sprite 位置为锚浮动；默认隐藏；鼠标进入 sprite + 操作栏整体 group 才显示；mouse leave 渐隐。
- **AC-7 跨 Space / 全屏浮动**：macOS 上切 Space 后形象仍可见；macOS 上启动全屏 app（如 QuickTime 全屏）→ 形象浮在全屏 app 之上（NSPanel + fullScreenAuxiliary 解，**issue 007 关闭**）；Win 上 F11 fullscreen 浏览器 → 形象仍可见（alwaysOnTop 天然支持）。
- **AC-8 015 全 9 条 AC 回归通过**：015 AC-1 ~ AC-9 在本期完成后全部跑通（含 owner / 双订阅 / 主动轮分流 / silent turn 丢弃 / sessionProjection 兼容 / 跨 Space / 零退化 / dev CLI）。
- **AC-9 016 全 12 条 AC 回归通过**：016 AC-1 ~ AC-12 在本期完成后全部跑通；其中 AC-1 物理尺寸口径由"240×320 固定窗"切到"monitors union 整屏"语义等价更新（用户感官等价），不视为退化。
- **AC-10 既有 chat / pet 行为零退化**：user 在 chat 窗输入对话全流程行为不变；pet 形象左键拖拽 / 透明区穿透 / 托盘菜单 / 操作栏点击"打开对话" 行为不变。
- **AC-11 一份代码 + cross-build 全绿**：`apply_pet_overlay_fullscreen` cross-platform 共用；setup hook NSPanel 转换 vs Win 整屏走各自 `#[cfg(target_os = "...")]` gate；`./scripts/check`（lint + typecheck + 单测）三平台全绿；`cargo build` 在 macOS / Win 都通。
- **AC-12 issue 007 + issue 008 关闭**：本期完成后 issue 007 + issue 008 都置 `resolved`，各补一行修复指向（commit / 本需求）。

---

## 7. 已知风险与监测项（不阻塞验收 / 不进 AC）

本节登记本期"已接受 / 监测中 / 不阻塞"的风险，供 design 阶段 + 实施期参考；任一项升级为"不可绕阻塞"时，按 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §6 反转触发条件处理（立新 ADR 覆盖 0004）。

| # | 风险 / 监测项 | 来源 | 处理 |
|---|---|---|---|
| 1 | macOS NSPanel + PixiJS WebGL 30fps cap 根因未充分诊断 | ADR 0004 §4.2 + macOS spike §6.1 | 沿用接受；如产品验证阶段实测感知不可接受，立专项 spike（沿 0004 §6 反转条件 5） |
| 2 | macOS 跨版本表现差异（RAM Sonoma 66MB / Sequoia 29MB / Tahoe 110MB） | ADR 0004 §4.3 + manasight 实测 | 沿用接受；跨版本设备覆盖留后续设备扩展 spike |
| 3 | Win 端 24h 长跑稳定性 + GDI 漏速跨场景对照（cursor 静止 vs 持续移动） | Win spike §6.4.3 "未补的数据" | 不作为 17a 硬门槛；建议产品稳定运行一段时间后做一次 24h 长跑复测 |
| 4 | Win 端 WebView2 / Win build 号采集（对照 wry#1691 复现窗口的精准取证） | Win spike §6.4.3 "未补的数据" | 不作为 17a 硬门槛；实施期顺手采集 |
| 5 | 多 DPR 多屏 sprite world position vs CSS px 坐标系映射 minor 异常 | Win spike §6.4.3 Minor 异常 + §6.4.4.9 takeaway 5 | 不在 17a 范围；转交 017b design 阶段处理（sprite world position vs CSS px 坐标系映射 + DOM 浮动 UI 层定位精确测试） |
| 6 | tauri#9373 在跨设备 / 跨 GPU driver / Win 10 环境复现性 | Win spike §6.5 反转阈值 2 | 用户端如撞复现 + 关键变量定位为整屏 transparent + alwaysOnTop + Tauri 特定组合 + 无 workaround → 触发 0004 §6 反转条件 3 |
| 7 | WebView2Feedback#5536 上游 fix ETA + WebView2 runtime regression | Win spike §6.6 长期跟踪项 | 后续 session 每 3-6 个月回看一次 |

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-15 | design 阶段澄清 PIXI 容器选型：R-4.2.2 / R-4.2.3 + §1.2 第 3 项 + §2 "可替换形象容器" In Scope 行 + AC-3 措辞由"PIXI Sprite + 占位贴图"放宽为"PIXI Container（avatar slot）+ 占位内容（Graphics + Text 等价重现 016 现有视觉）"。理由：Live2DModel 与 PIXI.Sprite 都是 PIXI.Container 的兄弟子类——17a 用 Container 作 avatar slot、17b 仅替换 slot 内 children 为 Live2DModel，5 个 17a/17b 接缝点中 4 个 plumbing 挂外层 Container 上、17b 不重接；Sprite 路径下要全部解绑/重接。 | §1.2 / §2 In Scope 表 / R-4.2.2 / R-4.2.3 / AC-3 |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-15
- **确认时间**：2026-06-15
- **关联决策**：[ADR 0004 · 桌宠承载形态与渲染路径](../../decisions/0004-pet-overlay-route/README.md)
- **关联需求**：[需求 010](../010-desktop-shell-and-chat-ui/) / [需求 015](../015-desktop-pet-bubble-and-conversation-owner/) / [需求 016](../016-pet-bubble-independent-window/)
- **关联 spike**（ground truth listing 来源）：
  - macOS：[`spike-tauri-nspanel-pet-window.md`](../../explorations/desktop-pet-form-factor/spike-tauri-nspanel-pet-window.md) §5.5
  - Win：[`spike-tauri-win-overlay-research.md`](../../explorations/desktop-pet-form-factor/spike-tauri-win-overlay-research.md) §6.4.4
- **关联 issue**（本期关闭）：[`007-macos-fullscreen-overlay-limit`](../../issues/007-macos-fullscreen-overlay-limit/) / [`008-pet-action-bar-hover-gate`](../../issues/008-pet-action-bar-hover-gate/)
- **下期承接**：017b 在本期底座上做形象内容替换（PIXI Sprite → Live2DModel）+ 状态机 + Codex 兼容 + lip-sync
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
