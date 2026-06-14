# Spike · OLV 源码深读

> Spike 记录，非决策、非需求。验证"OLV 这条路在 Electron 下端到端跑通的 plumbing"在 Tauri 下哪些可直接照搬、哪些 Electron 特有 API 需要找 Tauri 等价物。

- **创建时间**：2026-06-14
- **状态**：done(pass)（2026-06-14 完成）
- **依赖**：无（5 项 spike 中最先做的）
- **关联**：本目录 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §7.5 / §6 OLV 路线参考 / §2.2 OLV 调研事实
- **下游 spike**：[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md)（依赖本 spike 输出的 Electron → Tauri 映射认知）

---

## 1. 验证目标

回答两个问题：

1. **OLV 在 Electron 下整屏 transparent overlay 的具体实现路径**——pet 模式 `setBounds` 算多 display 并集 / canvas 整屏挂载 + DPR / `setIgnoreMouseEvents` / 拖 sprite / 滚轮缩放 / 模型切换，每一环具体怎么写、用了什么 Electron API。
2. **从 Electron 到 Tauri 的概念映射**——哪些是"概念层等价、直接迁移"、哪些是"Electron 特有 API 在 Tauri 没对应、需要 workaround 或 `#[cfg(target_os = "macos")]` 加料"。

## 2. 验证方法

**入口文件清单**（按 overlay-vs-windowed.md §2.2 已识别）：

- `src/main/window-manager.ts`——pet 模式 `setBounds` 整屏并集 + `setIgnoreMouseEvents` 切换 + 普通/pet 模式互转
- `src/renderer/src/hooks/canvas/use-live2d-resize.ts`——canvas DPR + model `_modelMatrix.scale` 缩放
- cursor passthrough 链路——alpha 采样 hitTest（`gl.readPixels` 1 像素方案在 OLV 里的具体实现）
- 拖 sprite 链路——PixiJS 事件 + `sprite.position` 改动 + sprite world position 是否需要 emit 给 Electron main
- 滚轮缩放——`kScale` / `MIN_SCALE=0.1` / `MAX_SCALE=5.0` 范围实现

**操作方式**：纯 GitHub 在线阅读，不 clone、不跑 Electron 端到端。每条链路读完后在本文档 §5 里产出"链路名 → 关键文件 + 关键函数 / 状态字段 + 实现要点（≤ 5 行）"的速记表，以及"Electron → Tauri 映射注释"表（如 `setIgnoreMouseEvents` → Tauri `WebviewWindow::set_ignore_cursor_events`）。

## 3. 通过条件

- **pass**：5 条链路（pet 模式 setBounds / canvas resize + DPR / cursor passthrough / 拖 sprite + 同步 / 滚轮缩放）每条都有一份速记 + Electron → Tauri 映射注释，且至少识别 1 处 Electron 特有 API（或主动确认"全部都有 Tauri 等价"）。
- **fail / 阻塞**：基本不会触发（纯阅读型 spike）。唯一可能阻塞的是 OLV 仓库被删 / 源码访问不到——此时降级为读 commit history / GitHub mirror。

## 4. 不在范围

- 不实测 OLV 代码（不 clone、不跑 Electron 端到端、不真跑 Live2D 模型）——本 spike 是阅读型。
- 不复盘 OLV 全部产品功能（ASR / TTS / 模型切换 UX 等），只读跟"整屏 overlay + Live2D 渲染 plumbing + cursor passthrough"相关的那部分。
- 不写跨平台行为推论——具体跨平台 spike 由 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) 负责。

## 5. 实际结果

### 5.1 5 条链路速记

#### 链路 1 · Pet 模式 setBounds 整屏并集

- **文件**：`src/main/window-manager.ts`（343 行）+ `src/main/index.ts`
- **入口**：`setWindowMode('pet')` → `setWindowModePet()`（同步部分：opacity 0、保存 windowedBounds、退出 fullscreen、setBackgroundColor `#00000000`、`setAlwaysOnTop(true, 'screen-saver')`、`setPosition(0, 0)`、`webContents.send('pre-mode-changed', 'pet')`）→ renderer ack `renderer-ready-for-mode-change` → `continueSetWindowModePet()` 算所有 display 并集
- **核心代码**（`continueSetWindowModePet`）：
  ```ts
  const displays = screen.getAllDisplays();
  const minX = Math.min(...displays.map((d) => d.bounds.x));
  const minY = Math.min(...displays.map((d) => d.bounds.y));
  const maxX = Math.max(...displays.map((d) => d.bounds.x + d.bounds.width));
  const maxY = Math.max(...displays.map((d) => d.bounds.y + d.bounds.height));
  this.window.setBounds({ x: minX, y: minY, width: maxX - minX, height: maxY - minY });
  ```
- **配套**：`setResizable(false)` / `setSkipTaskbar(true)` / `setFocusable(false)` / `setIgnoreMouseEvents(true)`（mac 不带 forward；非 mac 带 `{ forward: true }`）
- **macOS 加料**：`setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })`（跨 Space + 浮全屏 app 上层）

#### 链路 2 · Canvas resize + DPR

- **文件**：`src/renderer/src/hooks/canvas/use-live2d-resize.ts`（349 行）
- **handleResize**：pet 模式取 `window.innerWidth/innerHeight`、普通模式取 containerBounds；跑 `canvas.width = Math.round(width * devicePixelRatio)` + `canvas.style.width = `${width}px``；调 `LAppDelegate.getInstance().onResize()`
- **触发**：(a) `useEffect` on modelInfo.url 变化 / (b) ResizeObserver on container / (c) `window.addEventListener('resize', ...)` / (d) sidebar state 变化
- **跟链路 1 的配合**：pet 模式 `window.innerWidth/Height` 自动等于整屏 setBounds 后的 combined w/h → canvas 自动撑到所有 display 并集

#### 链路 3 · Cursor passthrough（component hover IPC）

- **文件**：
  - main：`src/main/window-manager.ts` `updateComponentHover` (lines 284-307) + `src/main/index.ts` `ipcMain.on('update-component-hover', ...)` (line 53)
  - renderer：`src/renderer/src/hooks/electron/use-draggable.ts`（DOM 组件用，101 行）+ `src/renderer/src/hooks/canvas/use-live2d-model.ts` lines 314-333（Live2D 模型用）
- **机制**：
  1. pet 模式默认整窗 `setIgnoreMouseEvents(true)`
  2. renderer 跟踪 hover：
     - DOM 组件：`onMouseEnter/Leave` → `window.api.updateComponentHover(componentId, true/false)` → IPC `update-component-hover`
     - Live2D 模型：`handleMouseMove` 里跑 `model.anyhitTest(modelX, modelY) !== null || model.isHitOnModel(modelX, modelY)`（Cubism Web SDK 内置 hit-area API），状态变化时 emit IPC
  3. main 端 `hoveringComponents: Set<string>`，set 空 → ignore=true（穿透），有 hover → ignore=false（接收）
- **macOS 致命限制**：mac 下 `setIgnoreMouseEvents(true)` 不带 `{ forward: true }` → mousemove 根本不到 renderer → hover 检测无法启动 → mac pet 模式下用户**不能直接 hover Live2D 模型让 passthrough 自动解开**
- **macOS 兜底**：`toggle-force-ignore-mouse` 全局快捷键（main/index.ts 注册）—— 用户按快捷键临时让整窗变成普通可交互窗，操作完再切回穿透。这是手动 mode toggle，不是自动检测
- **关键发现**：**OLV 没有用 alpha 采样 / `gl.readPixels` hitTest**——这跟 exploration §7.4 假设的方案不同。alpha 采样是 agent-friend 主动新增的 UX 升级路径，不是 OLV 路线已验证的方案

#### 链路 4 · Live2D 模型拖拽（canvas 内）

- **文件**：`src/renderer/src/hooks/canvas/use-live2d-model.ts`（542 行）+ `src/renderer/src/components/canvas/live2d.tsx`（130 行，组件 wiring）
- **handleMouseDown**：用 Cubism `model.anyhitTest(modelX, modelY)` + `model.isHitOnModel(modelX, modelY)` 判定鼠标在模型上 → 记 mouseDownTime/Pos 进入 "potential tap" 状态、保存 `modelStartPos = matrix[12,13]`
- **handleMouseMove**：
  - potential tap 状态下，移动距离 > 5px 或时间 > 200ms 且移动 > 1px → 升级为 drag
  - drag 中：屏幕坐标 → canvas scaled 坐标（× `canvas.width/canvas.clientWidth`）→ Cubism 模型坐标（`view._deviceToScreen.transformX/Y`）→ delta 写回 `model._modelMatrix.setMatrix(newMatrix)`（直接改 matrix[12] / [13] 平移分量）
- **handleMouseUp**：drag 状态 finalize position；若是 tap → 调 `model.startTapMotion(hitAreaName, modelInfo.tapMotions)` 播放点击 motion
- **关键发现**：**Cubism Web SDK 本身完全跨平台**，无 Electron 依赖——`anyhitTest` / `_deviceToScreen.transformX/Y` / `_modelMatrix.setMatrix` 在 Tauri 下零成本迁移。拖拽不是 PixiJS sprite，是 Cubism 模型矩阵直接平移

#### 链路 5 · 滚轮缩放

- **文件**：`src/renderer/src/hooks/canvas/use-live2d-resize.ts`（handleWheel + animateEase + applyScale）
- **常量**：`MIN_SCALE=0.1` / `MAX_SCALE=5.0` / `WHEEL_SCALE_STEP=0.03` / `EASING_FACTOR=0.3`
- **流程**：canvas wheel listener `{ passive: false }`（preventDefault 阻止页面滚动）→ deltaY 方向 ±0.03 → target scale clamp [0.1, 5.0] → `requestAnimationFrame(animateEase)` 线性插值（factor=0.3）current → target → `applyScale(newScale)` → `model._modelMatrix.scale(scale, scale)`
- **启用条件**：`modelInfo?.scrollToResize`（per-model 配置开关）
- **完全跨平台**，无 Electron 特异

### 5.2 Electron → Tauri API 等价表

| 链路 | Electron API | Tauri 等价 | macOS `#[cfg]` 加料 |
|---|---|---|---|
| 1 setBounds | `BrowserWindow.setBounds({x,y,w,h})` | `WebviewWindow::set_position` + `set_size` | 否 |
| 1 多 display 列举 | `screen.getAllDisplays()` | `AppHandle::monitors()` / `Monitor::size`/`position` | 否（Tauri 跨端等价） |
| 1 transparent | `transparent: true` + `setBackgroundColor('#00000000')` | tauri.conf.json `transparent: true` | 否 |
| 1 alwaysOnTop screen-saver level | `setAlwaysOnTop(true, 'screen-saver')` | `set_always_on_top(true)` + NSWindow `setLevel(NSScreenSaverWindowLevel)` | **是**（016 已落地） |
| 1 跨 Space + 浮全屏 | `setVisibleOnAllWorkspaces(true, {visibleOnFullScreen:true})` | NSWindow `setCollectionBehavior(canJoinAllSpaces \| fullScreenAuxiliary)` | **是**（016 已落地） |
| 1 skipTaskbar | `setSkipTaskbar(true)` | `set_skip_taskbar(true)` | 否 |
| 1 resizable / focusable | `setResizable / setFocusable(false)` | builder 期 `resizable(false)` / `focused(false)`，运行时 `set_resizable(false)` | 否（focused 是 builder 期） |
| 1 hasShadow | `hasShadow: false` | tauri.conf.json `shadow: false` | 否 |
| 1 fullscreen 退出 | `setFullScreen(false)` | `set_fullscreen(false)` | 否 |
| 2 DPR | `window.devicePixelRatio` | 同（webview JS） | 否 |
| 2 ResizeObserver / window resize | 同 webview JS | 同 | 否 |
| 3 setIgnoreMouseEvents | `setIgnoreMouseEvents(true, {forward:true})` | `set_ignore_cursor_events(true)` —— **Tauri 没有 `forward` 参数等价** | **待查**：Win / Linux 下 Tauri `set_ignore_cursor_events(true)` 时 mousemove 是否仍 forward 给 webview |
| 3 IPC hover 通知 | `ipcRenderer.send` + `ipcMain.on` | 前端 `invoke('update_component_hover', ...)` + Rust `#[tauri::command]` | 否 |
| 4 拖 model (Cubism) | Cubism `anyhitTest` / `_modelMatrix.setMatrix` / `view._deviceToScreen` | **Cubism SDK 跨平台，直接复用** | 否 |
| 4 React DOM event handler | 同 webview JS | 同 | 否 |
| 5 滚轮缩放 | canvas wheel listener + RAF | 同 | 否 |
| menu titleBarStyle | `'hiddenInset'` | tauri.conf.json `titleBarStyle: "Overlay"` | 否（Tauri 跨端抽象） |
| traffic light 切换 | `setWindowButtonVisibility(false)` | macOS only：NSWindow `setButtonHidden(true)` | **是** |

### 5.3 关键发现总结

1. **OLV 不用 alpha 采样** —— exploration §7.4 假设的"alpha 采样是 OLV 路线必需组件"不成立。alpha 采样是 agent-friend 自己想用的 UX 升级（让透明区精确穿透），OLV 实际用的是 component-based hover IPC。spike 3（alpha hitTest 性能）应当从"必需验证"重定位为"UX 升级路径验证"——见 §6 反馈。

2. **OLV macOS pet 模式有 known UX 缺陷** —— mousemove 不到 renderer 导致 Live2D 模型 hover 检测在 mac 不工作，用户只能靠 `toggle-force-ignore-mouse` 全局快捷键手动切。如果 agent-friend 不接受这个 UX，**必须**在 mac 上走 alpha 采样路线（或更底层的 macOS NSEvent global monitor，绕过 webview）—— 这正好让 alpha 采样的"UX 升级"价值具体化。

3. **Cubism Web SDK 完全跨平台** —— 拖拽链路（anyhitTest / _deviceToScreen / _modelMatrix.setMatrix）零 Electron 依赖。spike 4（sprite drag + Rust 同步）的工程风险评估应当下调：Cubism API 调用部分零迁移成本，剩下的 Rust IPC 同步成本和 016 现有 bubble_window.rs 16ms tick 路径同构。

4. **Tauri `set_ignore_cursor_events` 没有 `forward` 参数等价** —— 这是 spike 2 必须 spike 的新增不确定性点：Win / Linux 下 Tauri `set_ignore_cursor_events(true)` 时 mousemove 是否仍 forward 给 webview？如果不 forward，DOM 组件 hover 路径在 Win / Linux 上也跟 mac 一样不工作，整个 component-hover 路径全 platform fail——这种情况下 alpha 采样从"UX 升级"变回"必需"。

5. **macOS 加料明确清单** —— 走 OLV 路线在 Tauri 下需要的 `#[cfg(target_os = "macos")]` 加料有 3 处：(a) NSScreenSaverWindowLevel（016 已有）(b) setCollectionBehavior 跨 Space + 浮全屏（016 已有）(c) NSWindow setButtonHidden 隐藏 traffic light（新增）。其它链路全部跨平台。

### 5.4 阅读文件清单

- `src/main/window-manager.ts`（343 行）—— 整屏 setBounds / IgnoreMouseEvents / hover tracking
- `src/main/index.ts`（146 行）—— IPC handler 注册、app lifecycle
- `src/renderer/src/hooks/canvas/use-live2d-resize.ts`（349 行）—— canvas resize / DPR / 滚轮缩放
- `src/renderer/src/hooks/canvas/use-live2d-model.ts`（542 行）—— Cubism hit-test / 拖拽 / tap motion / pet hover
- `src/renderer/src/hooks/electron/use-draggable.ts`（101 行）—— DOM 组件 hover IPC + 通用拖动 hook
- `src/renderer/src/components/canvas/live2d.tsx`（130 行）—— 组件 wiring（canvas + handlers）

## 6. 下一步

### 6.1 spike 1 自身

- pass。5 条链路速记 + Electron → Tauri 等价表 + 关键发现已产出。本 spike 关闭。

### 6.2 推动 spike 2（Tauri overlay 跨平台）

带着本 spike 输出推进，重点验证：

- §5.2 等价表里 **`set_ignore_cursor_events(true)` 在 Tauri Win / Linux 下 mousemove forward 行为** —— 这是新增的关键不确定性点，决定 component-hover 路径在 Win / Linux 上是否同样需要 alpha 采样兜底
- 等价表里 **5 处 `#[cfg(target_os = "macos")]` 加料**（NSScreenSaverWindowLevel / setCollectionBehavior / setButtonHidden）—— 其中 2 处 016 已有现成代码可复用
- macOS throwaway 分支 4 场景验证（多屏 / Mission Control / Space / fullscreen）

### 6.3 反馈：建议修订 spike 3（alpha hitTest 性能）

**问题**：spike 3 现版 §1 把 alpha 采样定位为"OLV 路线 cursor passthrough 的必需组件"，但本 spike 证实 OLV 实际不用 alpha——alpha 是 agent-friend 自己想加的 UX 升级路径。

**建议修订点**：
- §1 验证目标：从"OLV 路线必需验证"改为"UX 升级路径验证 / mac 平台兜底验证"
- §3 通过条件 `fail` 分支：原写"alpha 性能跑不通 → 路线 A 整体不可行"，应改为"alpha 性能跑不通 → 路线 A 在 mac 上只能用 force-ignore-mouse 全局快捷键兜底（跟 OLV 现状一致），不阻塞路线 A 本身"
- §6 下一步：增加"如果 spike 2 证实 Tauri Win/Linux 下 mousemove 也不 forward，alpha 采样从 UX 升级回升为必需"的分支

是否同步修订留给用户决策（避免 scope creep），但反馈已记录在此。

### 6.4 反馈：spike 4（sprite drag + Rust 同步）风险评估下调

- Cubism Web SDK API 跨平台、零迁移成本
- Rust 侧位置同步跟 016 现有 `bubble_window.rs` 16ms tick 路径同构
- 工程风险从原"中-高"下调为"低-中"

### 6.5 触发 ADR 的条件检查

按 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §8 的回流条件，本 spike 单独不构成回流 ADR 条件 —— 还需要 spike 2/3/4/5 全部跑完 + 项目级锁定决心，才推 ADR。本 spike 输出降低了 ADR 推动时的不确定性。
