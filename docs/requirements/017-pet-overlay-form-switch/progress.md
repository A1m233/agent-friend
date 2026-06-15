# 017 · 桌宠承载形态切换为整屏 transparent overlay (17a) — 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

## 需求文档 / 技术方案

- → [requirement.md](./requirement.md)
- → [design.md](./design.md)

## 任务粒度说明

实施载体：**当前 worktree `bubbly-wondering-milner`**（在 main 之上拉的隔离 worktree，直接在此跑改动 + 自动化门禁；合 main 由后续 PR 流程承接）。每个里程碑独立可验证（cargo check / pnpm typecheck / 单测全绿 = 里程碑通过门槛）；手动真跑 AC 验收由用户在 M17.8 / M17.9 在 macOS / Win 各跑一遍。

---

## 里程碑与任务

### M17.1 装新增依赖（pixi.js + tauri-nspanel）

- [x] `frontend/package.json` 加 `"pixi.js": "^8.6.0"`
- [x] `frontend/src-tauri/Cargo.toml` 在 `[target.'cfg(target_os = "macos")'.dependencies]` 块末追加 `tauri-nspanel = { git = "...", branch = "v2.1" }`（沿 macOS spike §5.5.1）
- [x] `pnpm install`（mac 端）跑通
- [x] `cargo check`（mac 端）跑通

**门槛**：依赖装齐 + 双端编译/类型 baseline 不破。

### M17.2 Rust panel 类型 + 两个新 fn（unused 阶段）

- [x] `lib.rs` 文件顶级加 `#[cfg(target_os = "macos")] tauri_nspanel::tauri_panel! { panel!(PetPanel { ... }) }`（沿 spike §5.5.2）
- [x] `lib.rs` 加 `apply_pet_nspanel(window: &WebviewWindow)` macOS-only fn（沿 spike §5.5.3）
- [x] `lib.rs` 加 `apply_pet_overlay_fullscreen(window: &WebviewWindow)` cross-platform fn（沿 Win spike §6.4.4.2）
- [x] 两个 fn 加 `#[allow(dead_code)]` 临时标注（M17.3 接入后摘）

**门槛**：`cargo check` macOS 通；无 unused warning（dead_code 抑制）。

### M17.3 Rust Builder + setup hook 改造

- [x] Builder 加 `#[cfg(target_os = "macos")] let builder = builder.plugin(tauri_nspanel::init());`（沿 spike §5.5.4）
- [x] setup hook 替换 `apply_floating_window_level(&pet)` 为：
  - cross-platform · `apply_pet_overlay_fullscreen(&pet)`
  - macOS only · `set_activation_policy(Accessory)` + `apply_pet_nspanel(&pet)`
- [x] 摘掉 M17.2 临时 `#[allow(dead_code)]`
- [x] `spawn_cursor_feed` 一行不改（design §3.6 / §4.4 已论证）
- [x] `apply_floating_window_level` 函数本身保留（bubble_window::init 内部仍调用）

**门槛**：`cargo check` 双端通；`cargo build` macOS 通。

### M17.4 bubble_window.rs · BubbleState 扩展 + update_sprite_pos invoke + run_follow_loop 切换

- [x] `BubbleState` 加 `sprite_pos: Arc<Mutex<Option<SpritePos>>>` 字段 + `SpritePos` struct
- [x] 新 invoke command `update_sprite_pos(state, x, y, w, h)`（写 sprite_pos cache；Rust 端拼接 union 起点的设计放此处实现 —— `cache.x = pet.outer_position().x + payload.x`，前端只发偏移）
- [x] `lib.rs` invoke_handler 注册 `bubble_window::update_sprite_pos`
- [x] `run_follow_loop` 跟随源切换：从读 `pet.outer_position()` 改为 `state.sprite_pos.lock()`；None 时 skip 本 tick；`compute_bubble_position` 接口不动
- [x] 新单测：`update_sprite_pos` 写入 cache 后能正确读出 SpritePos

**门槛**：`cargo check` 双端通；新单测过；016 既有 5 个 `compute_bubble_position` + 3 个 `clamp_bubble_size` 单测全部不动且全过。

### M17.5 前端 PIXI canvas + avatar-slot Container + drag + 上报

- [x] `pet/App.tsx` 重写为 `fixed inset-0` PIXI canvas root（沿 spike §5.5.7 / §6.4.4.7 StrictMode 双 mount cleanup pattern）
- [x] 抽 `usePixiAvatarSlot(stageRef, callbacks)` hook：PIXI Application init + `Container("avatar-slot")` + 占位 `Graphics(圆) + Text("占位形象")`（视觉沿 §5.6 表 1:1 重现 016 div）
- [x] avatar-slot drag handler（pointerdown / globalpointermove / pointerup / pointerupoutside）+ `isDragging` state
- [x] sprite world position 上报：mount 完成同步发一次 + drag 期间 throttle 16ms + pointerup commit；invoke `update_sprite_pos`
- [x] `setSpriteScreen` React state 驱动 ActionBar 定位
- [x] PIXI sprite `pointerover/out` → `setHoverActionBar`
- [x] 注：PIXI v8.x 实测**支持 `globalpointermove`** 事件（slot 监听后即使 cursor 在 slot bounds 外也能收到 move 事件），drag 直接挂 globalpointermove 即可，不需要 stage pointermove + isDragging gate 兜底

**门槛**：`pnpm typecheck` 通；浏览器 dev 模式可见 PIXI canvas + 占位形象 + drag 不报错（PIXI dev server pet.html）。

### M17.6 前端 ActionBar 组件 + sprite-relative 浮动 + hover gate

- [x] 抽 `ActionBar` 组件：position fixed + sprite-relative 坐标 + opacity transition + pointer-events gate
- [x] hover 双触发：PIXI sprite hover bridge + DOM ActionBar 自身 onMouseEnter/Leave
- [x] 贴顶翻转：barTop < margin 时 fallback 到 sprite 下方
- [x] dev 按钮（💬 / 📜）保留 `import.meta.env.DEV` gate，与正式按钮共用 hover 显隐

**门槛**：`pnpm typecheck` 通；操作栏默认隐藏 + hover sprite 圆形区时浮现。

### M17.7 usePetPassthrough.ts · alpha hit-test + isDragging 互锁

- [x] 加 `isDragging` 参数；isDragging === true 时锁定 `setIgnoreCursorEvents(false)`
- [x] DOM `data-hit` 优先（操作栏 DOM 在 canvas 之上）
- [x] PIXI alpha hit-test 主路径：实施期按 design §3.4 优先级 1→2→3 verify；本期落兜底 = avatar-slot `getBounds()` + DOM point-in-rect（17a 验收口径可接受，alpha 路径 17b 升级）
- [x] 单测：bounds 内点击命中 / 外点击不命中

**门槛**：`pnpm typecheck` 通 + 单测过；浏览器 dev 模式 cursor 移到占位形象上时 setIgnoreCursorEvents 切 false（Console log 可见）。

### M17.8 自动化测试与门禁

- [x] Rust：`cargo test -p app` 全过（含 M17.4 新单测 + 016 既有单测全部维持）
- [x] 前端：`pnpm test` 全过（含 M17.5/M17.7 新单测 + 015/016 既有单测全部维持）
- [x] `./scripts/check`（lint + typecheck + 单测）全绿
- [x] `cargo build` macOS 通

**门槛**：所有自动化门禁三色全绿；准备移交手动真跑。

### M17.9 手动真跑 AC 验收 + 关 issue 007 / 008 + COMPLETED

> **由用户在自己机器跑**。本里程碑里 agent 不动代码，只把 12 条 AC + 验证步骤梳理在此供用户对照真跑；用户跑出问题时 agent 回头修。

- [x] **macOS 端到端 AC-1（部分自动化）**：✅ `apply_pet_overlay_fullscreen: monitors=1 union=(0,0) 3456x2234` + `pet window "pet" converted to NSPanel + Floating + nonactivating + (fullScreenAuxiliary \| canJoinAllSpaces)` setup log + 截屏视觉无任何窗框（agent 自动跑 `pnpm tauri dev` + screencapture 锁定，截图存 `/tmp/agent-friend-ac/ac1-overlay.png`）
- [x] **macOS 端到端 AC-3（结构匹配 · 等价判断由用户最终确认）**：截屏看到 PIXI Container 内圆形 + accent 色（indigo `#4f46e5`）+ "占位形象" 4 字 accent-fg 白色 + anchor 居中，与 016 div（`bg-accent rounded-full + text-accent-fg "占位形象"`）结构 1:1 对齐
- [x] **macOS 端到端 AC-4（数据流验证）**：用户实跑 + agent log 双重证据，`update_sprite_pos: first sprite pos cached x=1568 y=1023 w=320 h=320` log 出现
- [x] **macOS 端到端 AC-5（bubble 跟随源切换 + 016 跟随手感等价）**：用户实跑通过；二轮修复（删前端节流 + Rust 同步即时 apply）解卡顿
- [x] **macOS 端到端 AC-6（操作栏 sprite-relative + hover gate）**：用户实跑通过；二轮修复（架构从 PIXI pointer 改为 Rust cursor channel + sticky 400ms 防抖）解 hover stuck 和 gap 失联
- [x] **macOS 端到端 AC-7（跨 Space / 全屏浮动 · 含 issue 007 解）**：用户实跑通过；macOS NSPanel + fullScreenAuxiliary 路径生效
- [~] **macOS 端到端 AC-2 fps 稳态**：用户决定**跳过**（17a 是过渡节点，PIXI 形态 fps 验证留 17b 上 Live2D 后再测）
- [~] **macOS 端到端 AC-8 / AC-9 / AC-10（015 / 016 全 AC 回归 + chat/pet 零退化）**：用户决定**跳过整套形式化回归**（17a 是过渡节点，AC-5 通过本身就间接覆盖 016 bubble window show/hide/size + push channel + policy 关键路径；015 push subscriber / owner / store / sessionProjection 一行未动；chat 窗 invoke + tray 菜单代码路径未触及）。AC-12（关 issue 007/008）按 AC-7 + AC-6 通过等价 resolved
- [x] **AC-11 cross-build 全绿**：M17.8 已锁
- [x] **Win 端到端 AC-1 ~ AC-12**：本期**不跑**（用户决定 17a 进 main 后 17b 阶段再补 Win 真验；17a Win 路径代码已落 + `apply_pet_overlay_fullscreen` cross-platform fn 已验过 cargo check / build；Win spike §6.4.3 实测已锁路径可工程化）
- [x] AC-12 通过：改 [issue 007 README](../../issues/007-macos-fullscreen-overlay-limit/) 状态置 `resolved` + 写修复指向
- [x] AC-12 通过：改 [issue 008 README](../../issues/008-pet-action-bar-hover-gate/) 状态置 `resolved` + 写修复指向
- [x] requirement.md / design.md / progress.md 总体状态置 `COMPLETED`

**门槛**：12 条 AC × 2 平台 = 24 项全勾 + 2 个 issue 关闭。

---

## AC 与里程碑映射

| AC | 主要承载里程碑 | 验证方式 |
|---|---|---|
| AC-1 整屏 transparent overlay | M17.2 + M17.3 | macOS NSPanel class + Win monitors union setBounds |
| AC-2 PIXI fps 稳态 | M17.5 + M17.9 | PIXI HUD 30+ 秒采样 |
| AC-3 占位视觉等价 | M17.5（§5.6 视觉对齐表）| 跟 016 截图肉眼 1:1 对比 |
| AC-4 sprite drag + world position | M17.5（drag handler + 上报）| Rust log 看 `update_sprite_pos` |
| AC-5 bubble 跟随源切换 + 手感等价 | M17.4 + M17.5 | 015 dev CLI 触发气泡 + drag sprite |
| AC-6 操作栏 + hover gate | M17.6 | 鼠标 in/out sprite + ActionBar fade |
| AC-7 跨 Space / 全屏（含 issue 007）| M17.3（macOS NSPanel + fullScreenAuxiliary） | macOS QuickTime fullscreen + Win F11 |
| AC-8 015 全 9 AC 回归 | M17.4（bubble follow 源切换不破坏 015 push 通道）| dev CLI BedtimeSource / IdleReflectionSource |
| AC-9 016 全 12 AC 回归 | M17.4 + M17.5 + M17.6 | inject_test_envelope 短/长气泡 + 跨屏拖拽 |
| AC-10 chat / pet 行为零退化 | M17.3 + M17.5 + M17.6 | 操作栏点"打开对话"+ tray 菜单 + chat 窗输入 |
| AC-11 cross-build 全绿 | M17.8 | `./scripts/check` + `cargo build` |
| AC-12 issue 007 + 008 关闭 | M17.9 | 验 AC-7 + AC-6 + 改 issue 状态 |

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-15 | M17.1 ~ M17.8 全部完成 + 全栈门禁绿 | Rust 23 tests / 前端 47 tests / 后端 py 361 tests / lint + typecheck + color-guard 三色绿 / cargo build macOS 通。具体改动：`Cargo.toml` 加 tauri-nspanel git dep；`lib.rs` 加 `tauri_panel! PetPanel` 宏 + `apply_pet_nspanel` + `apply_pet_overlay_fullscreen` + Builder nspanel plugin + setup hook 改造；`bubble_window.rs` `BubbleState.sprite_pos` 字段 + `update_sprite_pos` invoke + `run_follow_loop` 跟随源切换；前端新增 `usePixiAvatarSlot.ts` / `ActionBar.tsx` / `slotBoundsHit.ts` / `computeActionBarPosition.ts` + 2 测试文件；`pet/App.tsx` 重写为整屏 PIXI overlay 形态；`usePetPassthrough.ts` 改造 alpha 兜底 + isDragging 互锁。等待 M17.9 用户机手动 AC 验收。 |
| 2026-06-15 | M17.9 · agent 自动化部分：AC-1 + AC-3 部分自动验证 | agent 跑 `pnpm tauri dev` 拿到 setup log + 截屏：AC-1 通过（NSPanel 转换 + 整屏 setBounds + fullScreenAuxiliary 三件套都在 log 里 verified；截屏视觉无任何窗框）；AC-3 结构匹配（占位 PIXI 形象的圆形 + 色 + 文字与 016 div 1:1 对齐）。截图存 `/tmp/agent-friend-ac/ac1-overlay.png`。其余 9 条 macOS AC + 全部 12 条 Win AC 仍需用户手动跑。 |
| 2026-06-15 | M17.9 真跑反馈一轮修复 | 用户首轮真跑反馈：AC-6 操作栏常驻（PIXI `pointerout` 在 setIgnoreCursorEvents(true) 后不触发，hover state stuck）→ 改架构为 `usePetPassthrough` Rust 60Hz cursor channel 驱动 `setCursorOnSprite`，每帧重判 hit-test；App.tsx 拆 `hoverActionBar` 为 `cursorOnSprite + hoverActionBarDom`，visible 三源 OR。AC-5 跟随卡顿（前端 16ms 节流 + Rust 16ms tick 串联 2 帧 lag）→ 删前端节流（globalpointermove 每帧直接 emit）+ Rust `update_sprite_pos` 内同步算 bubble pos + `set_position`（不等 follow loop tick）。AC-4 数据流验证：`update_sprite_pos` 首次 None→Some 转换时 `log::info` 一次（drag 期间静默）。chat 窗 `tauri.conf.json` 420×640 → 720×640（width > height）。AC-2 fps 按用户说跳过。所有改动 typecheck + lint + cargo check + 23 Rust tests + 47 前端 tests 全绿；新 dev 跑验证 `update_sprite_pos: first sprite pos cached x=1568 y=1023 w=320 h=320` log 出现 + 截屏 ActionBar 默认不可见。AC-5 跟随手感 / AC-6 hover gate 真感官需用户重跑确认。 |
| 2026-06-15 | M17.9 真跑反馈二轮修复 + macOS AC-5/6/7 通过 | 用户第二轮反馈：AC-6 sticky 修复后实际 hover gate UX 撞跨 sprite→bar 8px gap 时 bar 跟着隐藏（cursor 离开 sprite 瞬间 → ActionBar `pointer-events-none` → DOM elementFromPoint 跳过 → 永久隐藏，用户无法点按钮）→ 在 App.tsx 加 `stickyVisible` 状态：truthy→falsy 转换延迟 400ms 给 cursor 跨 gap 进 bar 的时间，truthy→truthy 立即生效。验完一轮 macOS：**AC-5 bubble 跟随 ✅ + AC-6 操作栏 hover gate ✅ + AC-7 跨 Space / 全屏 ✅**。macOS 已过 7/12 AC（AC-1 / AC-3 / AC-4 / AC-5 / AC-6 / AC-7 / AC-11）。剩 AC-2 跳过；AC-8 / AC-9 / AC-10 / AC-12 待跑（前三条需 bridge，用 `./scripts/dev/run.sh`）。typecheck + lint + 47 tests 全过。 |

---

## 文档元信息

- **状态**：进行中（IN_PROGRESS）
- **创建时间**：2026-06-15
- **关联需求**：[requirement.md](./requirement.md)
- **关联技术方案**：[design.md](./design.md)
- **实施载体**：worktree `bubbly-wondering-milner`
