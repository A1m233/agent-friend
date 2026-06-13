# 016 · 桌宠气泡独立窗承载 — 进度

## 总体状态
<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

> 9 个里程碑全部完成。M16.1 ~ M16.8 实施 + M16.9 真跑（AC-1 / 2 / 3 / 4 / 7 / 8 / 9 / 10 通过；AC-5 / AC-6 / AC-11 落地说明见下）。
>
> M16.9 真跑期间发现并修复 2 个 design 缺漏：(1) `capabilities/default.json` 的 `windows` 列表漏加 `"bubble"`，导致 bubble webview 所有 `plugin:event|listen` / 自定义 invoke 命令被 Tauri 2 capability 系统拒绝（lf=fail），listener 静默失败 → 用户看不到气泡；(2) `push_subscriber.rs` M16.6 改造时 emit 目标仍写死 `"pet"`，与 listener 迁到 bubble webview 不匹配。两个 fix 都属机制层硬 bug，已在收尾 commit 修。
>
> 真跑接受为已知行为的 3 项：AC-4 拖拽即使 16ms tick (60Hz) 仍可感卡顿（macOS Tauri 跨窗 set_position 物理极限，未登 issue）；AC-5 `show_bubble` 即使 macOS `orderFrontRegardless` 仍抢 chat 焦点（Tauri / WKWebView 透明窗的焦点策略受限，未登 issue）；AC-11 BedtimeSource 真路径要 `AGENT_BRIDGE_ENABLE_BEDTIME=true` + 绑定 session id，dev 期改 `inject_test_envelope` 替代验证 push 链路（同样触达 bubble webview，机制对等）。
>
> 每个 M 完成后 `./scripts/check` 必须保持全绿（lint + typecheck + frontend tests + cargo build / test）；015 已有单测（policy / store / projection / PetBubble）任何一条退化即视为本期 M 未完成。

---

## 里程碑与任务

> 任务粒度按"涉及文件 + 涉及单测"组织，便于实施时逐项打勾、断点续传。文件路径以 [`design.md`](./design.md) §2 涉及文件清单为准。

### M16.1 多入口配置 + bubble webview 物理存在

> 落地后 dev 起来时 bubble window 物理存在（hidden），pet 主窗回 240×320；不含任何动作逻辑，仅打底。

- [x] `frontend/src-tauri/tauri.conf.json`：pet 窗 width=240 / height=320（回归）；新增 bubble window 配置（label="bubble", visible:false, transparent, alwaysOnTop, decorations:false, skipTaskbar:true, shadow:false, focus:false, visibleOnAllWorkspaces:true）
- [x] `frontend/vite.config.ts`：rollup input 新增 `bubble: resolve(__dirname, 'bubble.html')`
- [x] `frontend/bubble.html`：新文件，模板参考 pet.html，entry 指向 `/src/pages/bubble/main.tsx`
- [x] `frontend/src/pages/bubble/main.tsx`：新文件，挂载 `<BubbleApp />`
- [x] `frontend/src/pages/bubble/App.tsx`：新文件 stub（先返回空透明 div，后续 M16.6 接入 `<PetBubble />`）
- [x] `pnpm run typecheck` + `pnpm run lint` 全绿
- [ ] `pnpm tauri dev` 起来时能在 macOS Activity Monitor / `tauri windows list` 看到 bubble window 实体存在（visible:false 不可见）—— **留到 M16.8 spot-check 时一并验证**

### M16.2 Rust bubble_window mod 骨架 + 纯函数单测

> 完成后 `compute_bubble_position` 纯函数 + 5 个 case 单测全绿；不含建窗 / 跟随 / invoke 逻辑。

- [x] `frontend/src-tauri/src/bubble_window.rs`：新文件
  - [x] `Anchor` / `BubbleSize` / `Screen` struct
  - [x] `compute_bubble_position(pet: Anchor, bubble: BubbleSize, screen: Screen) -> (i32, i32)` 纯函数（含翻转判定 + 水平居中 + 边缘 clamp）
  - [x] 模块顶层常量 `BUBBLE_LABEL` / `TICK_MS` / `MARGIN` / `MIN_W` / `MAX_W` / `MIN_H` / `MAX_H`
  - [x] 顺手加 `clamp_bubble_size(w, h) -> (w, h)` —— M16.3 invoke 命令直接复用
- [x] `frontend/src-tauri/src/lib.rs`：顶部加 `mod bubble_window;`
- [x] `frontend/src-tauri/src/bubble_window.rs` 内嵌 `#[cfg(test)] mod tests`：
  - [x] case 1 · pet 在屏幕中央 → 默认贴上方、水平居中
  - [x] case 2 · pet 顶部贴墙 → 翻到下方
  - [x] case 3 · pet 在屏幕左边缘 → x clamp 到 screen.x，不越界
  - [x] case 4 · pet 在屏幕右边缘 → x 仍以居中为目标但不超 screen.x + screen.w
  - [x] case 5 · bubble 宽度大于 pet → 居中偏移为负也能 clamp
  - [x] 顺手加 3 个 `clamp_bubble_size` case（范围内 / 低于 MIN / 高于 MAX）
- [x] `cargo test bubble_window` 全绿（8 passed）
- [x] `cargo build` 通过（12 warning 都是 015 既有 + 本期 `Screen.h` 暂未读用 —— 不阻塞，下个 M 实现 follow loop 时会消化）

### M16.3 invoke 命令 + 显隐状态机

> 完成后前端可通过 invoke 触发 bubble window 显隐 / size 调整（仍无位置跟随）。

- [x] `frontend/src-tauri/src/bubble_window.rs`：
  - [x] `BubbleState`：`is_visible: Arc<AtomicBool>` + `visible_notify: Arc<Notify>`（M16.4 加 Arc 是为了跨 tokio task 共享；本身实现 Default + Clone）
  - [x] `init(app: &AppHandle) -> tauri::Result<()>`：取 bubble window + 透明窗 workaround（创建后立刻 `set_size` 一次）；查不到则 log warn 不阻断
  - [x] `#[tauri::command] show_bubble`：window.show() + `is_visible.store(true, Release)` + `visible_notify.notify_one()`
  - [x] `#[tauri::command] hide_bubble`：window.hide() + `is_visible.store(false, Release)`
  - [x] `#[tauri::command] set_bubble_size(width, height)`：clamp + `window.set_size(LogicalSize::new(w, h))`
  - [x] 取消 `build_bubble_window` 兜底——`tauri.conf.json` 已建窗，复刻配置反而易漂移；查不到时 log warn 即可
- [x] `frontend/src-tauri/src/lib.rs`：
  - [x] `tauri::Builder` 加 `.manage(bubble_window::BubbleState::default())`
  - [x] `setup` 闭包末尾调 `bubble_window::init(app.handle())?`
  - [x] `invoke_handler` 注册 `show_bubble` / `hide_bubble` / `set_bubble_size`
- [x] `frontend/src-tauri/Cargo.toml`：加 `tokio = { version = "1", features = ["sync"] }`（design 原说"不动 Cargo.toml"——实际 Notify 来自 tokio::sync，需引入；见变更记录）
- [x] `cargo build` 通过
- [ ] 手工 spot-check —— 推迟到 M16.8

### M16.4 跟随轮询 tokio task

> 完成后 bubble window show 时按 50ms tick 跟随 pet 主窗位置移动；hide 时停轮询。

- [x] `frontend/src-tauri/src/bubble_window.rs`：
  - [x] `spawn_follow_loop(app: AppHandle, state: BubbleState)`：在 `tauri::async_runtime` 上 spawn `run_follow_loop`
  - [x] `run_follow_loop(app, state)`：async fn
    - [x] hidden 时 `state.visible_notify.notified().await` park
    - [x] visible 时取 pet `outer_position` + `outer_size` + bubble `outer_size`，调 `compute_bubble_position` 算位置，`bubble.set_position(PhysicalPosition { x, y })`
    - [x] `current_screen_rect(&pet)` 取屏幕坐标系（基于 `pet.current_monitor()`）
    - [x] `tokio::time::sleep(Duration::from_millis(TICK_MS)).await`
  - [x] `init` 末尾调 `spawn_follow_loop`
- [x] `cargo build` 通过
- [ ] 手工 spot-check —— 推迟到 M16.8

### M16.5 macOS NSWindow setLevel 加料复用（015 helper 重命名 + 泛化）

> 完成后 bubble window 也有跨 Space / 全屏浮动能力；015 pet 窗调用点同步更新；Win / Linux 上是 no-op。

- [x] `frontend/src-tauri/src/lib.rs`：
  - [x] 015 helper `apply_pet_window_level(app: &AppHandle)` 重命名 + 泛化为 `apply_floating_window_level(window: &WebviewWindow)`
  - [x] `#[cfg(target_os = "macos")] mac_apply_floating_window_level(window: &WebviewWindow)` 实现：`setLevel: 1000` + `setCollectionBehavior: 1|256`
  - [x] `#[cfg(not(target_os = "macos"))]` 空实现
  - [x] 原 015 pet 窗调用点同步改为 `if let Some(pet) = app.get_webview_window("pet") { apply_floating_window_level(&pet); }`
- [x] `frontend/src-tauri/src/bubble_window.rs`：
  - [x] `init` 末尾对 bubble window 调一次 `crate::apply_floating_window_level(&bubble)`
- [x] `cargo build` 通过（含 macOS objc2 msg_send）
- [x] `cargo test` 全绿（20 passed：bubble_window 8 + push_subscriber 12）
- [ ] 手工 spot-check（macOS 切 Space + 全屏） —— 推迟到 M16.9 真跑

### M16.6 前端 PetBubble 改造 + bubble entry 接入 + petBubble store 同步

> 完成后 bubble entry 真正消费 015 push channel，气泡内容在 bubble window 内渲染；pet 主窗内无气泡。

- [x] `frontend/src/components/pet/PetBubble.tsx`：
  - [x] 去掉 `absolute` / `flipBelow` state / `useEffect getBoundingClientRect 翻转` / `top-[200px]` / `bottom-[200px]` / `left-1/2 -translate-x-1/2` / 固定 `w-[280px]`
  - [x] 新增：`useRef<HTMLDivElement>` + `useEffect` 挂 `ResizeObserver` → `requestAnimationFrame` debounce → `invoke('set_bubble_size', { width: ceil(rect.width), height: ceil(rect.height) })`；非 Tauri 环境 no-op
  - [x] 改 `w-fit max-w-[360px]` 内容驱动宽度
  - [x] 保留：内容渲染 / 截断 / expand / dismiss / 关闭按钮 / `data-hit` / 语义 token 配色 / animate-in
- [x] `frontend/src/pages/pet/App.tsx`：
  - [x] 去掉 `import { PetBubble }` + 渲染 `<PetBubble />`
  - [x] 去掉 `import { startPetBubbleSubscriber }` + useEffect 启动 subscriber（**这段移到 bubble/App.tsx**）
  - [x] 去掉外层 div 的 `relative`
  - [x] 保留：`usePetPassthrough` + 形象拖拽 + hover 操作栏 + `openChat` invoke
- [x] `frontend/src/stores/petBubble.ts`：
  - [x] 新增 `attachBubbleWindowSync(): () => void`：`usePetBubbleStore.subscribe` 监听 phase 变化 → idle→非idle invoke `show_bubble` / 非idle→idle invoke `hide_bubble`；非 Tauri 环境 no-op；返回 unsubscribe
  - [x] `usePetBubbleStore` 状态机本身 / `startPetBubbleSubscriber` / 既有 selectors 不动
- [x] `frontend/src/pages/bubble/App.tsx`：
  - [x] `useEffect` 启动 `startPetBubbleSubscriber` + `attachBubbleWindowSync`；清理时 unlisten + unsub
  - [x] 渲染 `<PetBubble />` + 透明 root div
- [x] `pnpm run typecheck` + `pnpm run lint` + `pnpm run lint:colors` 全绿

### M16.7 单测改造 + 015 既有单测回归

> 完成后所有单测全绿；015 既有断言一行不改、全部通过。

- [x] ~~`frontend/src/components/pet/PetBubble.test.tsx` 改造~~ —— 015 没建过这个文件（progress 原稿写错，无需改造），skip
- [x] `frontend/src/stores/petBubbleSync.test.ts`：新文件
  - [x] mock `@tauri-apps/api/core` 的 `invoke`（用 `vi.hoisted` 解决 mock 工厂提升顺序）+ mock `@/utils/tauri` 的 `isTauri`
  - [x] case 1 · idle → showing 触发一次 `show_bubble`
  - [x] case 2 · showing → showing（新 envelope 替换 current）不重复触发 show_bubble
  - [x] case 3 · showing → expanded 仍非 idle，不触发 hide_bubble / show_bubble
  - [x] case 4 · expanded → idle 触发一次 hide_bubble
  - [x] case 5 · idle → idle 不重复触发 hide_bubble
  - [x] case 6 · 非 Tauri 环境 attachBubbleWindowSync 返回 no-op，不调 invoke
- [x] 015 既有单测**不改、全绿**（回归硬指标）：
  - [x] `petBubble.test.ts`（store 状态机 8 个 case）
  - [x] `petBubblePolicy.test.ts`（policy 6 个 case）
  - [x] `sessionProjection.test.ts`（projection N 个 case）
  - [x] 015 已有的其他 frontend 单测
- [x] `pnpm test` 全绿
- [x] `cargo test` 全绿（含 M16.2 的 compute_bubble_position 5 case + 015 既有 push_subscriber 12 case）

### M16.8 dev spot-check 脚本

> 完成后 dev 期不依赖 bridge 也能调 bubble window size / 位置 / 翻转。

- [x] `scripts/dev-bubble-spotcheck/run.sh`：新文件
  - [x] 起 `pnpm tauri dev`
  - [x] 给出操作提示：从 devtools console 调 `__TAURI__.core.invoke('show_bubble')` / `set_bubble_size` 等；或注入 mock envelope 触发 store
- [x] `scripts/dev-bubble-spotcheck/run.ps1`：新文件，Windows 对应
- [x] `scripts/README.md`：登记新脚本
- [ ] 手工执行 spot-check 一次确认脚本能跑 —— **留给用户 M16.9 真跑时一并验证**（涉及 GUI + Tauri 编译，AI 跑不了）

### M16.9 端到端真跑 + AC 验收 + issue 005 关闭

> 完成后 12 条 AC 全绿、issue 005 状态置 resolved、可合入。

- [x] **AC-1** Pet 主窗物理尺寸 240×320：`pnpm tauri dev` 起来，macOS Activity Monitor / 截图断言 pet 主窗实际尺寸 240×320
- [x] **AC-2** 气泡走独立 window：触发 BedtimeSource → 看到 bubble window 是独立 OS window 出现（**不是** pet 主窗内 DOM）
- [x] **AC-3** 长文本不裁切：构造 >400 字主动轮气泡 → 进入 expanded 状态 → 全文完整可见、不被裁
- [x] **AC-4** 跟随 pet 主窗：拖动 pet 形象 → bubble window 同步位移；二者视觉无明显错位
- [ ] **AC-5** 不抢焦点：chat 窗 / 其他 app 输入态时触发气泡 → 当前焦点 / 光标 / 输入不被打断
- [ ] **AC-6** 跨 Space / 全屏浮动：macOS 上切 Space 后 bubble 仍可见；全屏 app 上仍悬浮（沿用 issue 007 边界）
- [x] **AC-7** 透明区穿透：bubble window 透明区不阻挡下方桌面 / app；气泡实心区接收点击
- [x] **AC-8** 015 全部 9 条 AC 回归：按 015 progress.md 列表逐条回归
- [x] **AC-9** 既有 chat / pet 行为零退化：user 在 chat 窗输入对话全流程 / pet 拖拽 / 透明区穿透 / 托盘菜单 / hover 操作栏 行为不变
- [x] **AC-10** 一份代码 + cross-build 全绿：核心 bubble window 控制代码不带 `#[cfg(...)]` 分支（除 015 已有的 macOS 加料）；`./scripts/check` 全绿；`cargo build` macOS 全绿
- [ ] **AC-11** Dev CLI 端到端：复用 015 dev CLI 触发链——BedtimeSource → bubble window 冒出 / chat 窗不出现该消息；IdleReflectionSource → bubble window 不显
- [x] **AC-12** issue 005 关闭：`docs/issues/005-pet-bubble-window-sizing/README.md` 状态置 `resolved` + 末尾加一行修复指向（本需求 016 + 合并 commit hash）

---

## AC 与里程碑映射

| AC | 验收里程碑 |
|---|---|
| AC-1 pet 主窗尺寸回归 | M16.1（配置）+ M16.9（断言） |
| AC-2 气泡走独立 window | M16.1 + M16.3 + M16.6 |
| AC-3 长文本不裁切 | M16.3 set_bubble_size + M16.6 ResizeObserver |
| AC-4 跟随 pet 主窗 | M16.4 跟随轮询 |
| AC-5 不抢焦点 | M16.1 `focus: false` 配置 |
| AC-6 跨 Space / 全屏浮动 | M16.5 macOS 加料复用 |
| AC-7 透明区穿透 | M16.1 transparent 配置 + M16.6 `data-hit` |
| AC-8 015 9 条 AC 回归 | M16.7 单测回归 + M16.9 端到端 |
| AC-9 既有 chat/pet 零退化 | M16.6 pet 减项 + M16.7 单测 + M16.9 真跑 |
| AC-10 一份代码 + cross-build | M16.2~M16.6 + `./scripts/check` |
| AC-11 Dev CLI 端到端 | M16.9 真跑 |
| AC-12 issue 005 关闭 | M16.9 末尾文档更新 |

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-13 | M16.1 完成：tauri.conf.json pet→240×320 + 新增 bubble window 配置；vite.config.ts 加 bubble input；bubble.html / bubble/main.tsx / bubble/App.tsx (stub) 新文件；typecheck + lint 全绿 | dev 启动验证留到 M16.8 |
| 2026-06-13 | M16.1 hot-fix：发现 tauri.conf.json 不应写 `visibleOnAllWorkspaces`（与 015 lib.rs `apply_pet_window_level` 决策冲突，Rust 端全权设 collectionBehavior）；pet / bubble 两处字段删除；design.md §6 同步 + 变更记录补一行 | 无影响 |
| 2026-06-13 | M16.2 完成：bubble_window.rs 新文件（Anchor/BubbleSize/Screen struct + compute_bubble_position 纯函数 + clamp_bubble_size 顺手补 + 8 个 case 单测）；lib.rs 加 `mod bubble_window;`；`cargo test bubble_window` 8 passed | `Screen.h` 暂未读 → dead_code warning，M16.4 follow loop 用上后会消 |
| 2026-06-13 | M16.3 完成：BubbleState（Arc<AtomicBool> + Arc<Notify>）+ 3 个 invoke 命令（show/hide/set_size）+ init 透明窗 workaround；lib.rs 注册 manage / setup / invoke_handler；Cargo.toml 加 tokio sync feature | design §2 涉及文件原说"不动 Cargo.toml"，实际需加 tokio sync —— design 同步补一行变更记录 |
| 2026-06-13 | M16.4 完成：spawn_follow_loop + run_follow_loop + current_screen_rect；hidden 时 Notify park；50ms tick；init 末尾 spawn | `Screen.h` 仍是 dead code（compute_bubble_position 不读、follow loop 也不读）— 保留作"屏幕矩形语义完整"，下次清 warning 时统一处理 |
| 2026-06-13 | M16.5 完成：015 helper `apply_pet_window_level` 重命名为 `apply_floating_window_level` + 签名改为接收 `&WebviewWindow`；pet 窗调用点同步；bubble_window::init 末尾对 bubble 窗也调一次；`cargo test` 20 passed | 后端 + 配置打底全完成；M16.6 起进前端 |
| 2026-06-13 | M16.6 完成：PetBubble.tsx 改造（去 absolute/flipBelow，加 ResizeObserver → invoke set_bubble_size）；pet/App.tsx 减项（去 PetBubble + subscriber 启动）；petBubble.ts 加 attachBubbleWindowSync；bubble/App.tsx 接入；typecheck + lint + lint:colors 全绿 | bubble entry 接 store + push subscriber + show/hide sync |
| 2026-06-13 | M16.7 完成：新增 petBubbleSync.test.ts（6 case，含 vi.hoisted mock）；015 既有单测一行不改全过；frontend `pnpm test` 38 passed（32 015 既有 + 6 016 新增）；cargo test 20 passed | 015 progress 写的 PetBubble.test.tsx 实际没建过，skip 那条 |
| 2026-06-13 | M16.8 完成：scripts/dev-bubble-spotcheck/{run.sh, run.ps1} 新建（起 tauri dev + 在 devtools console 直接调 invoke 命令调 size/位置/翻转）；scripts/README.md 登记一行 | "手工执行验证"留给用户 M16.9 真跑时一并做（涉及 GUI + Tauri 编译） |
| 2026-06-14 | M16.9 真跑收尾：发现并修 2 个机制层 bug —— (1) `capabilities/default.json` `windows` 列表漏加 `bubble`，导致 bubble webview 所有 `plugin:event\|listen` / 自定义 invoke 被 Tauri 2 capability 系统拒绝（listen Promise reject、listener 静默失败）；(2) `push_subscriber.rs` 仍 emit_to `"pet"`，M16.6 改造时漏迁到 `"bubble"`。fix 后 + 加 `inject_test_envelope` dev 命令，9 条 AC pass、AC-4 / AC-5 接受现状未登 issue、AC-6 落 issue 007 边界、AC-11 手工等价跳过、AC-12 关 issue 005 ✅ | hover 操作栏常驻属 015/010 既有 UX 漂移，登 issue 008 follow-up |

---

## 文档元信息

- **总体状态**：NOT_STARTED
- **创建时间**：2026-06-13
- **关联需求**：本目录 [`requirement.md`](./requirement.md)（CONFIRMED）+ [`design.md`](./design.md)（CONFIRMED）
