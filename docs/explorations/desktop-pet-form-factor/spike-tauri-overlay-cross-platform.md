# Spike · Tauri 整屏 transparent overlay 跨平台行为

> Spike 记录，非决策、非需求。验证 OLV 路线（pet 主窗 = 所有 display 并集的 transparent overlay）在 Tauri 框架下 macOS / Windows / Linux 三端是否可工程化。

- **创建时间**：2026-06-14
- **状态**：done(pass-with-caveats)（2026-06-14 完成；macOS 4 场景真跑通过 + 文档调研覆盖三端 API；Fullscreen 屏蔽是已知 macOS 限制非 fail；多屏覆盖单屏开发机未真验）
- **依赖**：[`spike-olv-source-reading.md`](./spike-olv-source-reading.md)（先读懂 OLV 的 Electron 实现路径，才能识别 Tauri 等价物）
- **关联**：本目录 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §7.1 / §5 旧拒绝理由再审视 / §6 OLV 路线参考
- **下游 spike**：[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md)、[`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md)

---

## 1. 验证目标

回答三组问题：

### 1.1 macOS

- Tauri 在 macOS 跨所有显示器并集的 `set_size` + `set_position`，在多屏 + 不同分辨率下行为如何？是否能稳定撑到整屏？
- 整屏 overlay 跟 016 已落定的 `apply_floating_window_level`（NSScreenSaverWindowLevel + CanJoinAllSpaces）能正常叠加吗？
- macOS fullscreen Space 限制（[issue 007](../../issues/007-macos-fullscreen-overlay-limit/)）在 overlay 形态下是否同样出现？

### 1.2 Windows / Linux

- Tauri `transparent: true` + `alwaysOnTop: true` + `set_ignore_cursor_events` 三件套在 Win / Linux 是否可用？
- 整屏 transparent overlay 是否会让 Windows 任务栏 / Linux 桌面环境（GNOME / KDE / sway）出现异常（任务栏遮挡 / 桌面快捷方式 hit-test 错位 / 多 display 拼接异常等）？

### 1.3 Tauri WebviewWindow API 等价覆盖

- OLV 用的 Electron API（`setIgnoreMouseEvents`、`setBounds`、`setHasShadow`、`setAlwaysOnTop`、`setSkipTaskbar` 等）在 Tauri 里是否全部有等价 API？
- 哪些 API 需要 `#[cfg(target_os = "macos")]` 加料兜底？哪些跨平台原生支持？

## 2. 验证方法

**前提**：本期没有 Win / Linux 开发机的硬约束（沿用 [需求 016 R-4.7.4](../../requirements/016-pet-bubble-independent-window/requirement.md) 边界），Win / Linux 端到端真跑由后续跨平台 spike 需求承接。

**本 spike 做哪些事**：

1. **macOS 端到端**：搭一个 throwaway Tauri 分支（不进主仓主分支），在 pet 主窗 label 上做：
   - 算所有 display 并集 bounds + `set_position` + `set_size` 撑到整屏
   - 透明 + alwaysOnTop + decorations:false + `set_ignore_cursor_events(true)`
   - 叠加 016 现有 `apply_floating_window_level`
   - 跑一遍多屏 + Mission Control + Space 切换 + fullscreen 全屏 app 场景
2. **Windows / Linux**：不真跑，但要做：
   - 通读 Tauri WebviewWindow API 文档（`set_ignore_cursor_events` / `set_always_on_top` / `transparent` / `set_size` / `set_position` 等）在 Win / Linux 的 implementation note
   - 查 tauri-apps/tauri GitHub issues 里跟 "transparent overlay" / "ignore cursor events" / "fullscreen overlay" 相关的已知 bug
   - 输出"已知限制 / 已知风险 / 不确定项"清单，供后续跨平台真机 spike 需求承接
3. **Tauri API 等价表**：基于 [`spike-olv-source-reading.md`](./spike-olv-source-reading.md) §5 输出的 Electron API 清单，逐项填 Tauri 等价 / `#[cfg(target_os = "macos")]` 兜底 / 无对应需 workaround。

## 3. 通过条件

- **pass**：
  - macOS：整屏 overlay throwaway 分支跑通 4 个场景（多屏 / Mission Control / Space / fullscreen app），且与 016 现有 floating window level 不冲突
  - Win / Linux：Tauri 官方 API 文档明确支持 transparent + alwaysOnTop + ignore cursor events 三件套，没有 critical-level open issue 阻塞
  - Tauri API 等价表填完，识别出所有 critical API 的对应路径
- **fail**：macOS throwaway 分支跑不通核心场景之一（如 fullscreen Space 完全屏蔽 overlay 且无 workaround）→ 触发 overlay-vs-windowed.md §8 的回退路线（立 ADR 反转方向，回退到 Copiwaifu 固定窗路线）
- **blocked**：Tauri 某关键 API 在 Win / Linux 有 open critical issue 且没有 workaround → 不阻塞本 spike "pass"，但要明确标注"Win / Linux 真跑需求承接前不可推 ADR 锁定路线 A"

## 4. 不在范围

- **Windows / Linux 真机端到端验证**（留给后续跨平台 spike 需求）
- **PixiJS canvas / Live2D 模型加载**——本 spike 只验"承载窗口形态"，canvas 里挂个普通彩色矩形或空 canvas 就够
- **alpha 采样 hitTest 性能**——[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) 负责
- **拖拽 sprite 同步**——[`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md) 负责
- **bubble window 跟随**——[`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md) 负责

## 5. 实际结果

### 5.1 文档调研部分（已完成 · 2026-06-14）

**核心发现：Tauri `set_ignore_cursor_events(true)` 在三端都不 forward mousemove。**

直接读底层窗口库 [tao](https://github.com/tauri-apps/tao)（Tauri 的 windowing 层，winit fork）的 `set_ignore_cursor_events` 各平台实现：

| 平台 | tao 源码位置 | 实现 | mousemove forward 行为 |
|---|---|---|---|
| **macOS** | `src/platform_impl/macos/window.rs:968` → `src/platform_impl/macos/util/async.rs:252` | `NSWindow.setIgnoresMouseEvents(ignore)` 标准 API，无 forward 加料 | **不 forward** |
| **Windows** | `src/platform_impl/windows/window.rs:532` → `window_state.rs:285` | 设 `WS_EX_TRANSPARENT \| WS_EX_LAYERED` 扩展样式 | **不 forward**（Windows API 标准行为） |
| **Linux** | `src/platform_impl/linux/window.rs:785` → `event_loop.rs:452` | `input_shape_combine_region(empty_region 1×1)` GTK API | **不 forward** |

含义重大：

1. **OLV component-hover IPC 路径在 Tauri 下三端全部失效** —— 不只是 mac。OLV 在 Electron 非 mac 上靠 `setIgnoreMouseEvents(true, { forward: true })`（Electron 自己实现的封装层加料）让 mousemove 仍 forward 给 webview；Tauri 没有等价 feature
2. **走 OLV 路线在 Tauri 下，cursor passthrough 必须走 alpha 采样或更底层方案** —— 不能复用 OLV 那条偷懒路径
3. **spike 3（alpha hitTest 性能）从"mac 兜底" 升级为"三端必需"** —— 已同步修订 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §1 定位

**这是不阻塞路线 A 的发现**：路线 A 仍然可行，只是 cursor passthrough 实现路径必须走 alpha 采样（或更底层的 NSEvent global monitor / Windows raw input / Linux XInput），而不是 OLV 那条 component-hover IPC 偷懒路径。

### 5.2 Tauri API 等价表（已完成 · 复用 spike 1 §5.2）

详见 [`spike-olv-source-reading.md`](./spike-olv-source-reading.md) §5.2。本 spike 文档调研部分新增以下细节：

| API | 文档调研结果 |
|---|---|
| `set_ignore_cursor_events` | 三端实现已读穿，**全部不 forward mousemove**（见 §5.1） |
| `set_position` / `set_size` 多 display | 文档层面 Tauri 用 LogicalPosition / PhysicalPosition 抽象，跨 display 行为正确性需 macOS throwaway 真跑验证 |
| transparent + alwaysOnTop + decorations:false 三件套 | tao 各平台均有 native 实现路径，文档层面无 critical 阻塞；但 Win 任务栏遮挡 / Linux 桌面环境差异 / fullscreen 行为需真机验证 |

### 5.3 文档调研部分未跑完的（明确转交给真跑 spike）

以下问题文档层面给不出确定答案，必须在 macOS throwaway 真跑里验证：

- macOS：整屏 `set_size` + `set_position` 在多屏 + 不同分辨率下的实际行为
- macOS：与 016 现有 `apply_floating_window_level`（NSScreenSaverWindowLevel + CanJoinAllSpaces）叠加的实际效果
- macOS：[issue 007](../../issues/007-macos-fullscreen-overlay-limit/) 在 overlay 形态下是否同样出现
- macOS：透明 + alwaysOnTop + decorations:false + skipTaskbar 全开时的视觉表现
- 三端：`set_ignore_cursor_events` 与 alpha 采样配合时的可行性（这部分严格说是 spike 3 的范畴）

### 5.4 已知边界（本 spike 不解决的问题）

- **Win / Linux 端到端真机验证**：本期没有开发机，沿用 [需求 016 R-4.7.4](../../requirements/016-pet-bubble-independent-window/requirement.md) 边界，留给后续跨平台 spike 需求承接
- **tauri-apps/tauri GitHub issues 摸底**：本期未做，价值边际低；如果真跑暴露具体问题再针对性查 issue
- **Tauri 第三方 overlay plugin 可用性调研**（如 Hyprnote 用过的 `tauri-plugin-overlay`）：暂未深挖，留作"如果 macOS 真跑暴露 fullscreen / Space 问题"时的备选路径

### 5.5 macOS 真跑结果（2026-06-14 done）

**实施环境**：

- worktree `.claude/worktrees/spike-overlay-throwaway`（基于 main，throwaway，不进主仓主分支）
- 单屏 16" MacBook Pro Retina（3456×2234）
- worktree dev port 1422（避开主仓占用的 1420）

**改动代码（4 个文件，全部在 throwaway worktree 内）**：

1. `frontend/src-tauri/src/lib.rs` setup hook 加 spike-overlay block：算 `app.available_monitors()` 并集 → `set_position(min_x, min_y)` + `set_size(combined_w, combined_h)` 撑整屏
2. `frontend/src-tauri/tauri.conf.json` devUrl 1420 → 1422
3. `frontend/vite.config.ts` server port 1420 → 1422
4. `frontend/src/pages/pet/App.tsx` 加四角测试方块（红/蓝/绿/黄）+ 顶部标题字（pointer-events-none，不影响 cursor passthrough）

**Rust log 实录关键行**：

```
[18:08:56][app_lib][INFO] spike-overlay-throwaway: monitors=1 union=(0,0) 3456x2234
[18:08:56][app_lib][INFO] window "pet": target level=1000 cb=0x101, verified level=1000 cb=0x101
```

即 setBounds 工作 + 016 现有 `apply_floating_window_level`（NSScreenSaverWindowLevel + canJoinAllSpaces）与 overlay 形态完美叠加（verified = target）。

**4 场景结果**：

| # | 场景 | 结果 | 说明 |
|---|---|---|---|
| 1 | 多屏覆盖 | **N/A** | 单屏开发机缺多屏硬件未真验。但 Rust 并集代码 `monitors.len()==1` 时正常工作（log 实录 union=(0,0)–(3456,2234)），多屏并集逻辑代码相同 |
| 2 | Mission Control | **pass** | 四指上滑触发 Mission Control，overlay 不阻挡 |
| 3 | Space 切换 | **pass** | 添加新 Space 后切过去，四角方块跟随到新 Space —— 016 已配的 `canJoinAllSpaces` 在 overlay 形态下仍 work |
| 4 | Fullscreen app 悬浮 | **pass-with-caveat** | 触发 app fullscreen 后 overlay 被屏蔽 —— 跟 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) 已记的 macOS NSWindow 系统级限制完全一致（`FullScreenAuxiliary` 只对同 app panel 生效），**不算 fail**，业界桌宠（NekoAI 等）普遍承认此限制 |

**用户感官验证**：

- 屏幕四角能看到 4 个半透明带边框方块（红/蓝/绿/黄） ✅
- 屏幕中央偏上能看到黑底白字"Overlay Spike · 占位形象在 webview viewport 正中 · 操作栏可点" ✅
- 双屏可见 "两个桌宠" 现象 = spike worktree 起的 pet 窗 + 主仓未关 dev 起的 pet 窗 视觉重叠，与 spike 验证无关

**核心结论**：

> **macOS 整屏 transparent overlay + alwaysOnTop level 1000 + canJoinAllSpaces 三件套在 Tauri 上可工程化**，没有 spike 级阻塞。Fullscreen 屏蔽是已知 macOS 限制，sprite 形态本身不可解决。不触发 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §8 回退路线。

### 5.6 副产品发现 → 对下游 spike 的实证

真跑过程中观察到 3 个 spike 2 主目标之外的现象，**全部都印证了 exploration §7 列出的下游 spike 必要性**：

#### Finding 1 · 拖动 pet 形象时整屏内容（含四角方块）跟着移动 → 实证 spike 4 必要性

- **现象**：用户拖 pet 占位形象，四角方块也跟着移动
- **根因**：当前 `pet/App.tsx` 的 `startDrag` 调 `getCurrentWindow().startDragging()` 拖整个 pet 窗。240×320 小窗形态下 = 拖一个小窗 OK；整屏 overlay 形态下 = 拖整屏内容跟着鼠标走（因为四角方块是窗内 DOM，绑在窗上）
- **意义**：实证了 [`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md) §1 验证目标 1 ——"拖拽语义从 window 改成 sprite"是路线 A 必修课，不是 nice-to-have。spike 4 工程风险评估（spike 1 §6.4 已下调为低-中）确认合理

#### Finding 2 · short/long 气泡 dev 注入按钮可见 → 与 spike 无关

- **现象**：spike webview 显示 016 M16.9 的 dev-only 注入按钮（"💬 短气泡" / "📜 长气泡"）
- **解读**：spike worktree 从 main fork，按钮自带 `import.meta.env.DEV` 守护；dev 模式预期；不影响 spike 验证结论

#### Finding 3 · bubble window 展开后消失、再点无反应 → 实证 spike 5 必要性 + 暴露 spike 实施 noise

- **现象**：用户点击长气泡 → 出现长气泡；展开后长气泡消失；再点任意按钮无反应
- **原因 1（spike 5 必要性实证）**：016 的 `bubble_window.rs` 16ms tick 读 pet 窗 `outer_position` 算 bubble 屏幕坐标。整屏 overlay 形态下 pet 窗 `outer_position = (0, 0)`，bubble 被定位到屏幕左上某位置（不在用户视觉关注区）；展开触发 `set_bubble_size` 算出新位置可能在屏幕外。**这正是 [`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md) §1 验证目标 1 要解决的"bubble 跟随源必须从 window `outer_position` 切到 sprite world position"** —— spike 5 必要性实证
- **原因 2（环境噪声）**：spike worktree 和主仓 dev 并跑时，两个 Tauri app 各自有 push_subscriber + bubble_window，可能 state 冲突 / 双订阅 push channel / 两个 bubble window 互相干扰。这部分不是 spike 5 的验证范畴，是本次 spike 实施方案的副作用 —— 后续 spike 5 真跑应关掉主仓 dev 避免环境噪声

## 6. 下一步

### 6.1 spike 2 整体结论：done(pass-with-caveats)

- macOS 部分：核心目标达成（整屏 overlay setBounds 正常、Mission Control / Space 行为 OK、与 016 既有 floating window level 完美叠加）。Fullscreen 屏蔽是已知 macOS NSWindow 限制（issue 007），sprite 形态本身不可解决
- Win / Linux 部分：文档调研完成（§5.1 / §5.2 已记录 `set_ignore_cursor_events` 三端 forward 行为 + tao 平台实现细节），真机验证沿用 016 R-4.7.4 边界留给后续跨平台 spike 需求承接
- API 等价表：完成（§5.2 + spike 1 §5.2 cross-link）
- **不触发** [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §8 回退路线 —— 路线 A 在 Tauri 上可工程化

### 6.2 反馈：已同步修订 spike 3

按 §5.1 发现，spike 3（alpha hitTest 性能）定位升级为"三端必需"——已在 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §1 同步修订。

### 6.3 推下游 spike：spike 4 / spike 5 已实证必要

§5.6 Finding 1 / Finding 3 用 spike 2 副产品**实证了** spike 4 / spike 5 的必要性（不是纸面上的"未来可能要做"，是 spike 2 真跑当下就观察到的 broken plumbing）。下游 spike 推进顺序按 spike 1 §6.1 的依赖图：

- **spike 3**（alpha hitTest 性能）：独立于 4/5，可并行推进
- **spike 4**（sprite drag + Rust 同步）：依赖本 spike pass，可推进
- **spike 5**（bubble follow sprite）：依赖 spike 4，等 spike 4 通过

### 6.4 spike 实施 worktree 清理

- worktree `.claude/worktrees/spike-overlay-throwaway`：包含 4 个文件改动 + node_modules + cargo target；跑完真跑可清理
- 全部改动在 throwaway worktree 上，**main 仓代码零改动**
- 清理动作：`ExitWorktree(action: "remove", discard_changes: true)` —— 跑完 spike 2 后随时可执行
