# 017 · 桌宠承载形态切换为整屏 transparent overlay (17a) — 技术方案

> 把 016 落地的"240×320 固定 pet 主窗"切到 [ADR 0004](../../decisions/0004-pet-overlay-route/README.md) 锁定的"整屏 transparent overlay"路线，落 PIXI canvas + avatar slot Container + sprite world position 数据流 + 操作栏 sprite-relative DOM 浮动 + cursor 整屏穿透。两份 spike 已沉淀完整 ground truth listing（macOS [`spike §5.5`](../../explorations/desktop-pet-form-factor/spike-tauri-nspanel-pet-window.md) / Win [`spike §6.4.4`](../../explorations/desktop-pet-form-factor/spike-tauri-win-overlay-research.md)），本设计在此基础上**只做 cherry-pick + 接缝层决策**，不再立 17a 前置 spike。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已完成（Completed）

## 需求文档

→ [requirement.md](./requirement.md)

---

## 1. 设计目标回顾

承接 [requirement.md](./requirement.md) §2 In Scope 共 11 项 + §6 共 12 条 AC + §7 共 7 项已知风险，本设计的核心承诺是：

1. **物理形态切换**：pet 主窗 240×320 → monitors union 整屏；macOS 走 NSPanel，Win 走普通 Window + alwaysOnTop
2. **PIXI canvas + avatar slot Container**：在整屏 webview 内挂 PIXI v8 应用，形象用 `PIXI.Container("avatar-slot")` 承载，内部 children 由占位 `Graphics + Text` 等价重现 016 现有 div 视觉
3. **5 个接缝点 4 个挂外层 Container**：sprite world position 数据流 / cursor hit-test target / 状态机 hook 点 / 操作栏 hover bridge 全部挂在 avatar-slot Container 上；17b 仅替 slot 内 children 为 Live2DModel
4. **bubble 跟随源替换**：016 follow loop 跟随 anchor 由 `pet.outer_position()` 替换为前端上报的 sprite world position；`compute_bubble_position` + 单测全部不动
5. **既有路径回归 0 退化**：015 push 通道 / owner / policy / store / sessionProjection / 016 bubble window 显隐 + size 控制 + chat 窗 + tray 一行不动
6. **副产品 issue 关闭**：issue 007（macOS NSPanel + fullScreenAuxiliary 自然解）+ issue 008（操作栏改造顺手解 hover gate）

---

## 2. 整体改动地图

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `frontend/src-tauri/Cargo.toml` | 新增 | `[target.'cfg(target_os = "macos")'.dependencies]` 块加 `tauri-nspanel = { git = "https://github.com/ahkohd/tauri-nspanel", branch = "v2.1" }`（沿 macOS spike §5.5.1） |
| `frontend/src-tauri/src/lib.rs` | 增 + 改 | 加 `tauri_panel! { PetPanel ... }` 宏（macOS only，文件顶级）；加 `apply_pet_nspanel` fn（macOS only）；加 `apply_pet_overlay_fullscreen` cross-platform fn（monitors union setBounds）；Builder 加 `.plugin(tauri_nspanel::init())`（macOS only）；setup hook 改造（macOS NSPanel 路径 / Win 整屏 alwaysOnTop）；`spawn_cursor_feed` **不动** |
| `frontend/src-tauri/src/bubble_window.rs` | 改 | `BubbleState` 加 `sprite_pos: Arc<Mutex<Option<SpritePos>>>`；新增 invoke command `update_sprite_pos(x, y, w, h, scale)`；`run_follow_loop` 跟随源由 `pet.outer_position()` 替换为读 `sprite_pos` cache；`compute_bubble_position` + 全部单测**不动** |
| `frontend/src/pages/pet/App.tsx` | 重写 | 整屏 `fixed inset-0` PIXI canvas + `PIXI.Container("avatar-slot")` + 占位 `Graphics + Text` + drag handler + sprite world position 上报；DOM 操作栏 sprite-relative + hover gate（顺手解 [issue 008](../../issues/008-pet-action-bar-hover-gate/)） |
| `frontend/src/pages/pet/usePetPassthrough.ts` | 改 | DOM `elementFromPoint` hit-test 替换为 PIXI alpha readPixels（兜底 = avatar-slot `getBounds()` + DOM point-in-rect）；isDragging 期间锁定 `setIgnoreCursorEvents(false)`；操作栏 DOM `data-hit` 兜底保留 |
| `frontend/src-tauri/tauri.conf.json` | 不动 | pet 窗 `width=240/height=320` 不动（setup hook 立即覆盖整屏）；`transparent + alwaysOnTop + decorations:false + skipTaskbar:true + shadow:false` 不动；顶层 `macOSPrivateApi: true` 不动 |
| `frontend/package.json` | 新增 | `pixi.js: ^8.6.0`（沿 Win spike §6.4.4.6 已实装 8.19.0） |

**严格不动**（plumbing freeze 清单，作回归门槛）：

- 015：`push_subscriber.rs` / `apply_floating_window_level` 函数体本身（仍由 bubble window init 调用）/ 前端 `usePetBubbleStore` / `petBubblePolicy` / `sessionProjection` / chat 窗对话流端到端
- 016：`bubble_window.rs` 的 `BubbleState.is_visible / visible_notify` 字段 / `show_bubble` / `hide_bubble` / `set_bubble_size` / `compute_bubble_position` / `clamp_bubble_size` / 全部单测
- `spawn_cursor_feed` 一行不改（详见 §3.6）
- chat 窗 / tray 菜单 / `open_chat` invoke command 一行不改

---

## 3. 架构决策

### 3.1 形象容器选型 · PIXI.Container 作 avatar slot

PIXI 类型继承事实：`Sprite extends Container`、`Live2DModel extends Container`——两者是兄弟子类。两种结构对比：

```
方案 A · Sprite 直接换：
  17a:  stage > Sprite(占位 texture)
  17b:  stage > Live2DModel  (Sprite 删掉,所有 plumbing 解绑/重接)

方案 B · Container 作 avatar slot（本设计选）：
  17a:  stage > Container("avatar-slot") > { Graphics(圆) + Text("占位形象") }
  17b:  stage > Container("avatar-slot") > { Live2DModel }   (slot 不动,只换内部 children)
```

5 个 17a/17b 接缝点（详见 §7）：

| # | 接缝点 | 方案 A · Sprite | 方案 B · Container（本设计） |
|---|---|---|---|
| 1 | 形象内容（slot 内 children） | 删 Sprite + 加 Live2DModel | 删占位 children + 加 Live2DModel |
| 2 | sprite world position 数据流 | 重接 drag handler 到新对象 | **挂外层 Container,不动** |
| 3 | cursor alpha hit-test target | 改 hit-test 目标对象引用 | **target 始终是外层 Container,不动** |
| 4 | 状态机 hook 点（17b 接） | 重接事件接口到新对象 | **挂外层 Container,17b 接收即可** |
| 5 | 操作栏 hover bridge | 重接 pointerover/out | **挂外层 Container,不动** |

→ 选方案 B（Container slot）。理由：4/5 接缝 17b plumbing 不重接，漏接点从 4 处降到 0。

### 3.2 sprite world position 数据流方向 · 事件驱动 + Rust 缓存

**方向**：前端 PIXI → invoke `update_sprite_pos(x, y, w, h, scale)` → Rust `BubbleState.sprite_pos` 缓存 → bubble follow loop 16ms tick 读 cache → 算 bubble position → `set_position`。Rust 侧**不主动**知道 sprite 在哪。

**频率**：事件驱动，不固定 tick：

| 触发时机 | 行为 |
|---|---|
| PIXI app mount 完成 | 同步发一次（首次 cache 填充，避免 bubble follow loop 早起 tick 读到 None） |
| `pointermove` during drag | 节流后发（每 16ms 至多 1 次） |
| `pointerup` (drag 结束) | 立即发一次 commit（确保最终位置精确同步） |
| 屏幕配置变化 / DPR 变化 / 多屏切换 | 不前端发；Rust 侧每 tick 自行 `current_screen_rect()` 重算（沿 016 现有逻辑） |

**节流不固定 60Hz tick** 的好处：idle 期 0 invoke 跨 IPC，节能；drag 期天然 60Hz（pointermove 频率与 monitor refresh 同步）。

**race 兜底**：bubble follow loop tick 读到 `sprite_pos = None`（PIXI 还没首次上报）→ skip 本 tick（沿 016 loop 已有"中间环节失败 → skip"模式），不动 bubble position；下次 tick 重试。

### 3.3 操作栏选型 · DOM absolute 而非 PIXI Container

DOM `<Button>` 复用 015/016 antd 组件 + 交互天然（hover / focus / a11y / Tab 焦点导航）。位置由 React state 监听 sprite world position，转 CSS px 渲染 `<div style={{ position: "fixed", left: spriteX, top: spriteY - barHeight - margin }}>`。

PIXI 内做按钮交互需自实现 hover / focus / 文字按钮 / 圆角阴影 / Tab 焦点 — 工作量大且偏离 015/016 已定的 UI 体系。**操作栏不进 PIXI canvas**。

### 3.4 cursor hit-test · PIXI alpha readPixels + 矩形兜底

**主路径**：沿 [`spike-alpha-hittest-perf.md`](../../explorations/desktop-pet-form-factor/spike-alpha-hittest-perf.md) §1 实证（readPixels < 1μs），`usePetPassthrough` 收到 `pet://cursor` 事件后，先在 PIXI canvas 该点采 alpha → > 阈值即 sprite 实心区命中。

**PIXI v8 API verify**：spike 验证时 PIXI 版本未明（可能是 v7 或 v8 早期）；v8 后期 `app.renderer.gl` 部分 internal 收紧，`readPixels` 不一定直接暴露。**实施期 verify 路径**：

1. 优先用 `app.renderer.extract.pixels(container)` 拿一帧 RGBA buffer 后单点采样（v8 推荐）
2. 失败回到 `app.renderer.gl.readPixels`（直接 WebGL context 调用，v8 可能仍 work）
3. 失败 → 走兜底（见下）

**兜底**：avatar-slot Container `getBounds()` + DOM point-in-rect。占位形象是圆形，bounding box 比 alpha 多命中约 21% 角区——17a 验收阶段可接受（占位形象本身就是粗糙占位，不要求像素级精确）；17b 真上 Live2D 时如 alpha readPixels 在 v8 仍不可用，再升级 plumbing。

**操作栏 DOM 兜底**：操作栏 DOM 节点上加 `data-hit`。DOM 在 PIXI canvas 之上，DOM elementFromPoint(`closest("[data-hit]")`) 仍可正确判定操作栏命中（不依赖 PIXI alpha 路径）。

### 3.5 drag 期间 cursor passthrough 互锁

**问题**：cursor passthrough 在 sprite 实心区切 off（`setIgnoreCursorEvents(false)` → webview 接管 → PIXI 接 pointermove）；drag 中用户快速划过空白区切 on（webview 不拦截 → PIXI 失去 pointermove）→ drag 中途断。

**解决**：React 加 `isDragging` state（PIXI sprite `pointerdown` → true / `pointerup` → false / `pointerupoutside` → false）。`usePetPassthrough` 在 `isDragging === true` 时**短路 hit-test**，锁定 `setIgnoreCursorEvents(false)` 不再 toggle。drag 结束 → `isDragging = false` → 恢复正常 hit-test 流。

**为什么不引入新数据流**：isDragging 已经是 React 内部 state；usePetPassthrough hook 只读 state（参数化或 ref 传入）。不增 IPC、不增 Rust 状态。

### 3.6 跨平台 cfg-gate 边界

**两端共用**：

- `apply_pet_overlay_fullscreen(window)` cross-platform fn（monitors union setBounds 算法，沿 spike §5.5.5 + Win spike §6.4.4.2 同款）
- 前端 PIXI canvas 整改、avatar-slot Container、drag handler、操作栏 DOM、cursor passthrough 切换、bubble follow source 替换 全部一份代码

**两端 cfg-gate 各自落**：

```rust
// setup hook 内
if let Some(pet) = app.get_webview_window("pet") {
    apply_pet_overlay_fullscreen(&pet);  // cross-platform: setBounds monitors union
    #[cfg(target_os = "macos")]
    {
        let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
        apply_pet_nspanel(&pet);  // tauri-nspanel 路径
    }
    // Win / Linux: tauri.conf.json 已配 alwaysOnTop + skipTaskbar + transparent，无加料
}
```

**spawn_cursor_feed 一行不改**：`pet.outer_position()` 整屏后是 monitors union 起点 `(min_x, min_y)`（不一定是 `(0, 0)`，多屏左屏 x 可能 < 0），公式 `(cursor.x - pos.x) / scale` 含义保持"光标相对 pet content 区的逻辑坐标"——前端 PIXI canvas `resizeTo: stage` 后 stage 大小 = monitors union 物理像素 / scale = 整屏逻辑像素，坐标系一致。

---

## 4. Rust 侧改动

### 4.1 `Cargo.toml` 新增依赖

`[target.'cfg(target_os = "macos")'.dependencies]` 块末追加（沿 macOS spike §5.5.1，不改其他依赖）：

```toml
# 0004 · pet 窗切 NSPanel 路径（业界 macOS Tauri overlay 标准方案）
tauri-nspanel = { git = "https://github.com/ahkohd/tauri-nspanel", branch = "v2.1" }
```

**`pixi.js`** 在 `frontend/package.json` 加 `^8.6.0`（不锁 lock 文件版本，pnpm install 自动 resolve；沿 Win spike §6.4.4.6 实装版本 8.19.0）。

### 4.2 `lib.rs` · 顶级 panel 类型 + 两个新 fn

**panel 类型声明**（文件顶级，`mod` 之前；沿 macOS spike §5.5.2 完整 listing）：

```rust
#[cfg(target_os = "macos")]
tauri_nspanel::tauri_panel! {
    panel!(PetPanel {
        config: {
            can_become_key_window: true,
            is_floating_panel: true
        }
    })
}
```

**`apply_pet_nspanel` fn**（沿 macOS spike §5.5.3 完整 listing；macOS only；用 `tauri_nspanel::WebviewWindowExt::to_panel<PetPanel>()` + 设 PanelLevel::Floating + nonactivating + fullScreenAuxiliary | canJoinAllSpaces）。

**`apply_pet_overlay_fullscreen` fn**（沿 Win spike §6.4.4.2 完整 listing；cross-platform 共用；算 `available_monitors()` union → `set_position` + `set_size`；失败仅 log warn）。

→ 两份 listing 是物理 ground truth，**直接 cherry-pick**，本 design 不复述代码。

### 4.3 `lib.rs` · Builder + setup hook 改造

**Builder 链**（macOS 加 nspanel plugin；沿 macOS spike §5.5.4）：

```rust
let builder = tauri::Builder::default()
    .manage(bubble_window::BubbleState::default());

#[cfg(target_os = "macos")]
let builder = builder.plugin(tauri_nspanel::init());
```

**setup hook 改造**（替换原 `apply_floating_window_level(&pet)` 调用）：

```rust
.setup(|app| {
    if cfg!(debug_assertions) {
        app.handle().plugin(tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info).build())?;
    }
    setup_tray(app.handle())?;
    spawn_cursor_feed(app.handle());
    push_subscriber::spawn_push_subscriber(app.handle(), bridge_base_url());

    if let Some(pet) = app.get_webview_window("pet") {
        // 1. cross-platform · 整屏 setBounds（monitors union）
        apply_pet_overlay_fullscreen(&pet);

        // 2. macOS · ActivationPolicy::Accessory + NSPanel 转换
        #[cfg(target_os = "macos")]
        {
            let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            apply_pet_nspanel(&pet);
            // 注：apply_floating_window_level 不再对 pet 调用（NSPanel 路径已等价覆盖
            //     setLevel + collectionBehavior）；apply_floating_window_level 函数
            //     本身保留，bubble_window::init 内部仍调用它对 bubble 窗加料。
        }
        // Win / Linux: tauri.conf.json 已配 alwaysOnTop + skipTaskbar + transparent；
        //              整屏 setBounds 后无需额外加料（Win spike §6.4.3 实证）
    }

    bubble_window::init(app.handle())?;
    Ok(())
})
```

**关键顺序**（实测验证，沿 macOS spike §5.5.5）：

1. `set_activation_policy` 必须先于 `to_panel`（apply_pet_nspanel 内部）—— 否则 Dock/Cmd+Tab 一闪而过
2. `setBounds`（apply_pet_overlay_fullscreen 内部 `set_position` + `set_size`）必须先于 `to_panel` —— to_panel 后窗已是 NSPanel，setBounds 行为不确定

### 4.4 `spawn_cursor_feed` 不动

整屏后 `pet.outer_position()` = monitors union 起点 `(min_x, min_y)`，公式 `(cursor.x - pos.x) / scale` 仍正确表达"光标相对 pet content 区的逻辑坐标"——前端 PIXI canvas resizeTo viewport 后 stage 大小 = 整屏逻辑像素，坐标系一致。`spawn_cursor_feed` 原代码一行不改。

### 4.5 `bubble_window.rs` · `BubbleState` + `update_sprite_pos`

**`BubbleState` 结构扩展**：

```rust
use std::sync::Mutex;

#[derive(Debug, Clone, Copy)]
pub struct SpritePos {
    pub x: i32,    // 屏幕物理坐标系（左上原点；可能 < 0 多屏场景）
    pub y: i32,
    pub w: u32,    // sprite 当前 bounding box 物理像素
    pub h: u32,
}

#[derive(Clone)]
pub struct BubbleState {
    pub is_visible: Arc<AtomicBool>,
    pub visible_notify: Arc<Notify>,
    /// 17a · 前端 PIXI sprite world position 缓存
    /// drag 期间 / mount 完成 / drag commit 时由前端 invoke `update_sprite_pos` 写入；
    /// run_follow_loop 16ms tick 读它代替 016 的 `pet.outer_position()`。
    pub sprite_pos: Arc<Mutex<Option<SpritePos>>>,
}

impl Default for BubbleState {
    fn default() -> Self {
        Self {
            is_visible: Arc::new(AtomicBool::new(false)),
            visible_notify: Arc::new(Notify::new()),
            sprite_pos: Arc::new(Mutex::new(None)),
        }
    }
}
```

**新 invoke command**：

```rust
/// 17a · 前端 PIXI sprite world position 上报。
///
/// 触发时机：(1) PIXI app mount 完成首次上报；(2) drag pointermove 节流后；
/// (3) drag pointerup commit。idle 期不发。
///
/// 坐标系：屏幕物理坐标系，左上原点；多屏场景 x 可能 < 0。
/// w/h 是 sprite bounding box 物理像素（avatar-slot Container `getBounds()` 转
/// scale_factor 后的物理尺寸）。
#[tauri::command]
pub fn update_sprite_pos(
    state: tauri::State<'_, BubbleState>,
    x: i32, y: i32, w: u32, h: u32,
) -> Result<(), String> {
    let mut guard = state.sprite_pos.lock().map_err(|e| e.to_string())?;
    *guard = Some(SpritePos { x, y, w, h });
    Ok(())
}
```

注册（macOS / Win 同；debug + release 都注册）：

```rust
let builder = builder.invoke_handler(tauri::generate_handler![
    open_chat,
    bubble_window::show_bubble,
    bubble_window::hide_bubble,
    bubble_window::set_bubble_size,
    bubble_window::update_sprite_pos,  // ← 17a 新增
    #[cfg(debug_assertions)]
    bubble_window::inject_test_envelope,
]);
```

### 4.6 `bubble_window.rs` · `run_follow_loop` 跟随源切换

**改动**：原从 `pet.outer_position()` + `pet.outer_size()` 取 anchor，改为从 `state.sprite_pos` 取。`compute_bubble_position` 接口 `Anchor { x, y, w, h }` 不动；只换数据源。

```rust
async fn run_follow_loop(app: AppHandle, state: BubbleState) {
    loop {
        if !state.is_visible.load(Ordering::Acquire) {
            state.visible_notify.notified().await;
            continue;
        }

        let sprite = state.sprite_pos.lock().ok().and_then(|g| *g);
        let Some(sp) = sprite else {
            // 17a · race 兜底：前端 PIXI 还没首次上报 → skip 本 tick
            tokio::time::sleep(Duration::from_millis(TICK_MS)).await;
            continue;
        };

        if let (Some(pet), Some(bubble)) = (
            app.get_webview_window("pet"),
            app.get_webview_window(BUBBLE_LABEL),
        ) {
            if let (Ok(bubble_size), Some(screen)) = (
                bubble.outer_size(),
                current_screen_rect(&pet),
            ) {
                let anchor = Anchor { x: sp.x, y: sp.y, w: sp.w, h: sp.h };
                let bub = BubbleSize { w: bubble_size.width, h: bubble_size.height };
                let (x, y) = compute_bubble_position(anchor, bub, screen);
                let _ = bubble.set_position(PhysicalPosition { x, y });
            }
        }

        tokio::time::sleep(Duration::from_millis(TICK_MS)).await;
    }
}
```

`current_screen_rect(&pet)` 仍用 pet 窗（整屏 NSPanel 也有 `current_monitor()`，返主屏）；如未来需要"sprite 跨屏 → bubble 跟到同屏"，由 17a/17b 后续 design 增量处理（本期不做）。

`compute_bubble_position` + 全部单测**完全不动**——anchor 接口稳定，单测验证的几何行为不依赖数据源。

---

## 5. 前端 PIXI canvas 整改

### 5.1 `pet/App.tsx` 整体结构

```tsx
import { useEffect, useRef, useState, type RefObject } from "react";
import * as PIXI from "pixi.js";
import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import { Button } from "@/components/ui";
import { usePetPassthrough } from "./usePetPassthrough";

export function PetApp() {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [spriteScreen, setSpriteScreen] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [hoverActionBar, setHoverActionBar] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  // PIXI app + avatar slot 生命周期 + drag handler + sprite world position 上报
  usePixiAvatarSlot(stageRef, { setSpriteScreen, setHoverActionBar, setIsDragging });

  // cursor passthrough（PIXI alpha hit-test + isDragging 互锁）
  usePetPassthrough({ stageRef, isDragging });

  return (
    <div ref={stageRef} className="fixed inset-0 overflow-hidden bg-transparent">
      {/* PIXI canvas 由 hook 在 effect 内 appendChild 进 stageRef */}
      {spriteScreen && (
        <ActionBar
          spriteScreen={spriteScreen}
          visible={hoverActionBar}
          onMouseEnter={() => setHoverActionBar(true)}
          onMouseLeave={() => setHoverActionBar(false)}
        />
      )}
    </div>
  );
}
```

**StrictMode 双 mount cleanup**（沿 macOS spike §5.5.7 + Win spike §6.4.4.7 同款 pattern）：`usePixiAvatarSlot` 内部 `cancelled` flag + `pixi.destroy(true, { children: true, texture: true })`。

### 5.2 PIXI Container avatar-slot + 占位内容

```tsx
// usePixiAvatarSlot 内 init 段
const slot = new PIXI.Container();
slot.label = "avatar-slot";
slot.eventMode = "static";   // 接 pointer 事件供 drag / hover bridge

// 占位内容 children：等价重现 016 现有 div(圆形 bg-accent + "占位形象" 4 字)
const circle = new PIXI.Graphics();
circle.circle(0, 0, 80).fill({ color: 0x[accent-color] });  // accent 色取自 016 tailwind theme
const text = new PIXI.Text({
  text: "占位形象",
  style: { fontSize: 16, fill: 0x[accent-fg-color] },
});
text.anchor.set(0.5);
slot.addChild(circle);
slot.addChild(text);

// 初始位置 = stage 中心
slot.x = pixi.screen.width / 2;
slot.y = pixi.screen.height / 2;

pixi.stage.addChild(slot);
```

`accent` / `accent-fg` 颜色值实施期从 `tailwind.config.js` 取（保证视觉 1:1 对齐 016 现状）。

**17b 替换路径**：删 `circle` + `text`，`slot.addChild(await Live2DModel.from(modelJson))`。slot Container 不动，drag handler / pointer 事件 / world position plumbing 全部继承。

### 5.3 sprite drag + world position 上报

```tsx
// drag state
let dragStartPointer: { x: number; y: number } | null = null;
let dragStartSlot: { x: number; y: number } | null = null;

slot.on("pointerdown", (e) => {
  setIsDragging(true);
  dragStartPointer = { x: e.global.x, y: e.global.y };
  dragStartSlot = { x: slot.x, y: slot.y };
});

slot.on("globalpointermove", (e) => {
  if (!dragStartPointer || !dragStartSlot) return;
  slot.x = dragStartSlot.x + (e.global.x - dragStartPointer.x);
  slot.y = dragStartSlot.y + (e.global.y - dragStartPointer.y);
  emitSpritePosThrottled(slot, pixi);
});

const endDrag = () => {
  if (!dragStartPointer) return;
  setIsDragging(false);
  dragStartPointer = null;
  dragStartSlot = null;
  emitSpritePosImmediate(slot, pixi);  // commit
};
slot.on("pointerup", endDrag);
slot.on("pointerupoutside", endDrag);

// PIXI app mount 完成同步发一次（避免 bubble follow loop 早起 tick 读到 None）
emitSpritePosImmediate(slot, pixi);
```

**`emitSpritePos` 实现**（节流 throttle ~16ms）：

```ts
function emitSpritePosImmediate(slot: PIXI.Container, pixi: PIXI.Application) {
  // PIXI logical px → 屏幕物理 px：乘 devicePixelRatio + 加 webview viewport 起点
  // pet 窗整屏后 outer_position = monitors union 起点（由 Rust 端在 setup hook 设置）；
  // PIXI stage 坐标系原点 = pet content 区左上 = monitors union 起点
  // → sprite world position（屏幕物理坐标）= union_origin + slot.x/y * dpr
  const dpr = window.devicePixelRatio || 1;
  const bounds = slot.getBounds();
  // setSpriteScreen 用于 React state 驱动操作栏定位（CSS px,与 webview viewport 同坐标系）
  setSpriteScreen({ x: slot.x, y: slot.y, w: bounds.width, h: bounds.height });
  // 上报 Rust：Rust 端会拼上 union 起点（由 pet.outer_position 提供）
  void invoke("update_sprite_pos", {
    x: Math.round(slot.x * dpr),
    y: Math.round(slot.y * dpr),
    w: Math.round(bounds.width * dpr),
    h: Math.round(bounds.height * dpr),
  });
}
```

**坐标系映射的关键点**：

- PIXI stage 坐标系：logical px，原点 = webview viewport 左上 = pet 窗 outer_position（整屏后 = monitors union 起点）
- Rust `update_sprite_pos` 接收的 (x, y) 应为**屏幕物理坐标**（与 016 `Anchor.x/y` 一致）
- 两者差异：①乘 DPR ②加 union 起点
- ②由 Rust 端在 `update_sprite_pos` 写入 cache 时拼接：`cache.x = pet.outer_position().x + payload.x`（前端只发"相对 pet content 区"的偏移；Rust 端补 union 起点）

→ 实施期 design.md 落代码时把 ②的拼接放在 Rust `update_sprite_pos` 内（更稳，前端不需要知道整屏起点）。

### 5.4 操作栏 sprite-relative DOM 浮动 + hover gate

```tsx
function ActionBar({ spriteScreen, visible, onMouseEnter, onMouseLeave }) {
  // CSS px 坐标 = sprite world (CSS px) - bar 自身高度 - 间距
  const barTop = spriteScreen.y - 60 /* bar 估高 */ - 8 /* margin */;
  const barLeft = spriteScreen.x;
  return (
    <div
      data-hit
      style={{ position: "fixed", left: barLeft, top: Math.max(0, barTop) }}
      className={`flex flex-col items-center gap-1 transition-opacity ${visible ? "opacity-100" : "opacity-0"} pointer-events-${visible ? "auto" : "none"}`}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <Button data-hit variant="outline" size="pill" onClick={openChat}>打开对话界面</Button>
      {import.meta.env.DEV && <DevButtons />}
    </div>
  );
}
```

**hover gate 来源**（解 [issue 008](../../issues/008-pet-action-bar-hover-gate/)）：`hoverActionBar` state 由两路触发——

1. **PIXI sprite hover bridge**：avatar-slot Container 上 `pointerover/pointerout` → `setHoverActionBar(true/false)`
2. **DOM 操作栏自身 hover**：`ActionBar` 上 `onMouseEnter/onMouseLeave` → 维持 true（鼠标从 sprite 移到 bar 时不闪）

**贴墙翻转**：barTop < 0 时 fallback 到 sprite 下方（`spriteScreen.y + spriteScreen.h + 8`）；与 016 `compute_bubble_position` 翻转语义一致（视觉上"操作栏挂 sprite 上 / 屏顶贴墙翻到下方"）。

### 5.5 cursor hit-test plumbing 切到 PIXI alpha

`usePetPassthrough` 改造（沿 §3.4 主路径 + 兜底 + §3.5 isDragging 互锁）：

```ts
export function usePetPassthrough({
  stageRef,
  isDragging,
}: { stageRef: RefObject<HTMLDivElement | null>; isDragging: boolean }) {
  useEffect(() => {
    if (!isTauri()) return;
    const win = getCurrentWindow();
    let ignored: boolean | null = null;
    let disposed = false;

    const apply = (shouldIgnore: boolean) => {
      if (shouldIgnore === ignored) return;
      ignored = shouldIgnore;
      void win.setIgnoreCursorEvents(shouldIgnore);
    };

    apply(true);  // 初始穿透

    const unlisten = listen<{ x: number; y: number }>("pet://cursor", (e) => {
      if (disposed) return;
      // §3.5 · drag 期间锁定 false
      if (isDragging) return apply(false);
      // 操作栏 DOM data-hit 优先（DOM 在 canvas 之上）
      const domHit = !!document.elementFromPoint(e.payload.x, e.payload.y)?.closest("[data-hit]");
      if (domHit) return apply(false);
      // PIXI alpha hit-test（§3.4 主路径 → bounding box 兜底）
      const pixiHit = pixiAlphaHitTest(e.payload.x, e.payload.y) ?? slotBoundsHit(e.payload.x, e.payload.y);
      apply(!pixiHit);
    });

    return () => { disposed = true; void unlisten.then(f => f()); void win.setIgnoreCursorEvents(false); };
  }, [isDragging, stageRef]);
}
```

`pixiAlphaHitTest` 实施期 verify（按 §3.4 优先级 1→2→3 顺序），失败回到 `slotBoundsHit`（avatar-slot `getBounds()` + point-in-rect）。

### 5.6 占位形象的视觉对齐细节

**016 现状**：

```tsx
<div id="pet-stage" data-hit className="grid h-40 w-40 cursor-grab select-none place-items-center rounded-full bg-accent text-accent-fg shadow-lg active:cursor-grabbing">
  占位形象
</div>
```

→ 圆形（h-40 w-40 rounded-full = 直径 160 logical px）+ bg-accent + 中央 "占位形象" 4 字 + accent-fg 文字色 + shadow-lg。

**17a PIXI 等价重现**：

| 016 DOM 属性 | PIXI 等价实现 |
|---|---|
| `h-40 w-40 rounded-full` | `PIXI.Graphics.circle(0, 0, 80).fill(...)`（半径 80 = 直径 160） |
| `bg-accent` | `fill({ color: <tailwind accent hex> })` |
| `text-accent-fg` 文字色 | `PIXI.Text style.fill = <tailwind accent-fg hex>` |
| 居中"占位形象" | `text.anchor.set(0.5)` + `text.x = text.y = 0`（与 circle 同坐标系） |
| `shadow-lg` | `PIXI.DropShadowFilter`（pixi-filters 包；如复杂度过高，本期 fallback 不画 shadow，由 17b 上 Live2D 时统一处理形象阴影） |
| `cursor-grab` / `active:cursor-grabbing` | DOM stageRef 容器上 `className="cursor-grab"` + drag 期间切 `cursor-grabbing`（PIXI canvas 不强制 OS cursor，沿用 webview viewport 容器 CSS） |

**shadow 兜底**：如不引 pixi-filters，本期占位形象不画 shadow；视觉差异轻微（占位本就是粗糙占位），AC-3 验收口径"等价重现"包含此 trade-off；17b 上 Live2D 时形象本身有阴影/物理，无需补。

---

## 6. `tauri.conf.json` + `package.json` 改动

**`tauri.conf.json` 不动**（macOS spike §5.5.6 已确认）：

- pet 窗 `width=240/height=320` 保持，setup hook 立即覆盖整屏（不影响最终行为）
- pet 窗 `transparent + alwaysOnTop + decorations:false + skipTaskbar:true + shadow:false + fullscreen:false` 不动
- 顶层 `"macOSPrivateApi": true` 不动（[ADR 0004](../../decisions/0004-pet-overlay-route/README.md) §4.1 接受不上 MAS）
- bubble / chat 窗配置完全不动

**`package.json`** 加 `pixi.js: ^8.6.0`；`pnpm install` 自动 resolve `pnpm-lock.yaml`。

---

## 7. 17a/17b 接缝点显式列表（供 17b 接力）

| # | 接缝点 | 17a 落地 | 17b 接 |
|---|---|---|---|
| 1 | 形象内容（avatar-slot 内 children） | `Graphics(circle) + Text("占位形象")` 两 child | 删两 child + `slot.addChild(await Live2DModel.from(modelJson))`（slot 不动） |
| 2 | sprite world position 数据流 | drag 期间 `pointermove`/`pointerup` invoke `update_sprite_pos` | **不动**（slot.x/y 由 17b 状态机 / motion 系统更新；emitSpritePos hook 仍挂 slot） |
| 3 | cursor alpha hit-test target | PIXI alpha readPixels（occupies slot 区域） | **不动**（alpha 采样目标仍是 slot；Live2D 内部 alpha 透明区由 SDK 渲染） |
| 4 | 状态机 hook 点 | slot 上**预留 `slot.label` + 单态 idle**（无切换） | 接 015 push event flow → slot 上加 `setState(idle/thinking/speaking/error)` → 派发到 Live2DModel motion / expression |
| 5 | 操作栏 hover bridge | slot `pointerover/out` → React `setHoverActionBar` | **不动** |

**17b 立项时 design.md** 需在此基础上明确：

- Live2D 库选型（pixi-live2d-display vs easy-live2d，需 PIXI v8 兼容性 verify）
- 模型文件存放路径 / 加载策略
- 状态机驱动机制（与 015 push event 流的对齐）
- lip-sync 接口形态（与 014 audio output 通道的耦合）
- Codex 兼容点（与 engine event 的对齐）

→ 这 5 项**不在 17a 范围**，本设计不展开。

---

## 8. 测试策略

### 8.1 Rust 单测

- **`bubble_window::compute_bubble_position` 全部 5 个 case 不动**（数据源切换不改变几何行为）。
- **新增 1 个单测**验 `update_sprite_pos` 写入 + `run_follow_loop` 读到正确 anchor：mock `BubbleState`，先 `update_sprite_pos(100, 200, 160, 160)`，再 lock 读 `sprite_pos`，断言 `Some(SpritePos { x: 100, y: 200, w: 160, h: 160 })`。
- **新增 1 个单测**验 race 兜底：`sprite_pos = None` 时 `run_follow_loop` 一帧不动 bubble position（用 mock app + flag）—— 实施期判断单测复杂度，简单则补，复杂则改手动验。

### 8.2 前端单测

- **`usePixiAvatarSlot` StrictMode 双 mount cleanup**：testing-library + `<React.StrictMode>` wrap 反复 mount/unmount，断言 PIXI app destroy 调用次数 = mount 次数（无残留 GPU context；vitest mock PIXI.Application 的 destroy 拦截）。
- **`pixiAlphaHitTest` 兜底路径**：mock pixi 抛错 → 走 `slotBoundsHit` → 在 slot bounds 内点击返 true，外点击返 false。
- **`ActionBar` 翻转**：`spriteScreen.y < bar.height + margin` 时 barTop fallback 到 sprite 下方。

### 8.3 手动真跑端到端

| AC | 验证方式 | 平台 |
|---|---|---|
| AC-1 整屏 transparent overlay | macOS：截图确认无窗框 + Activity Monitor 看 pet window class = NSPanel；Win：F12 dev tools 看 outerWidth = monitors union | macOS + Win |
| AC-2 fps 稳态 | macOS: PIXI HUD 30+ 秒采样 ≥ 30fps；Win: ≥ 55fps | macOS + Win |
| AC-3 占位视觉等价 | 跟 016 截图肉眼对比 1:1（圆形 + 文字位置 / 颜色 / 大小） | macOS（视觉对齐）+ Win（同步等价） |
| AC-4 sprite drag + world position | drag sprite + 看 Rust log `update_sprite_pos: x=... y=...` | macOS + Win |
| AC-5 bubble 跟随手感 | 015 dev CLI 触发气泡 + drag sprite → bubble 同步跟随 | macOS + Win |
| AC-6 操作栏 hover gate | 鼠标进入 sprite 圆形区 → 操作栏 fade in；离开 → fade out | macOS + Win |
| AC-7 跨 Space / 全屏 | macOS：切 Space + QuickTime fullscreen；Win：F11 浏览器 fullscreen | macOS + Win |
| AC-8 015 全 9 AC 回归 | dev CLI BedtimeSource / IdleReflectionSource 触发 + sessionProjection 对话流 | macOS（端到端）+ Win（关键路径） |
| AC-9 016 全 12 AC 回归 | inject_test_envelope 短/长气泡 + 跨屏拖拽 + bubble dismiss | macOS + Win |
| AC-10 chat / pet 行为零退化 | 操作栏点"打开对话"+ tray 菜单 + chat 窗输入对话 | macOS + Win |
| AC-11 cross-build 全绿 | `./scripts/check` + `cargo build` macOS / Win | macOS + Win |
| AC-12 issue 007 + 008 关闭 | 验 AC-7（007）+ AC-6（008），手动改 issue 状态 + 写修复指向 commit | — |

---

## 9. 影响分析

### 9.1 上下游影响

- **上游 015 / 016 接口**：零改动；`BubbleState` 加字段是结构体扩展，原 `is_visible` / `visible_notify` 字段语义不变；invoke 命令名 `show_bubble` / `hide_bubble` / `set_bubble_size` 不变。
- **下游 17b**：5 个接缝点已沉淀（§7），17b 立项时直接接力。
- **chat / tray / push subscriber / sessionProjection / petBubblePolicy / usePetBubbleStore / `<PetBubble />`**：全部不动。
- **dev CLI 端到端**：015 BedtimeSource / IdleReflectionSource + 016 inject_test_envelope 路径不动。

### 9.2 风险点

| # | 风险 | 概率 / 影响 | 缓解 |
|---|---|---|---|
| 1 | PIXI v8 alpha readPixels API 在 spike 验证窗口外不可用 | 中 / 中 | §3.4 兜底 = avatar-slot getBounds + DOM point-in-rect；本期可接受粗糙；17b 升级 plumbing |
| 2 | macOS 30fps cap 在产品验证阶段实测感知不可接受 | 低 / 高 | ADR 0004 §4.2 已接受 trade-off；如撞，沿 0004 §6 反转条件 5 立专项 spike |
| 3 | sprite world position → bubble follow loop race（前端 PIXI mount 慢于 Rust setup tick） | 高 / 低 | §3.2 已落兜底（None → skip 本 tick）；mount 完成立即 emit 一次首次同步 |
| 4 | drag 期间 cursor passthrough 互锁的边缘 case（pointer 在 webview 外释放） | 低 / 中 | `pointerupoutside` 同样触发 endDrag；如 OS 级 cursor 出 webview 边界（e.g. drag 出整屏 monitors union 起点上方）→ 沿 PIXI globalpointermove 行为，超 viewport 后无事件，drag 卡住但不 crash；用户重新进 webview 范围或 ESC 兜底（实施期可加 ESC 监听 endDrag）|
| 5 | 操作栏贴顶翻转 vs sprite 拖拽快移 抖动 | 低 / 低 | barTop < 0 一次性翻转到下方；不在边界附近反复跳；如出现，加 hysteresis（margin = 16px 而非 8px）|
| 6 | shadow / Live2D 物理在 17b 上线前缺少 | 低 / 低（占位本就粗糙）| §5.6 fallback 不画 shadow；17b 上 Live2D 时形象自带 |
| 7 | tauri-nspanel git dep branch v2.1 与 Tauri 2.11.2 兼容性 | 低 / 中 | macOS spike §5.5 实测验证通过（spike-tauri-nspanel-pet-window done(pass-with-caveats)）；如 Tauri 升级触发不兼容，nspanel 本身按 git branch 锁版本，不主动升 |

### 9.3 跨平台行为

| 维度 | macOS | Win | Linux |
|---|---|---|---|
| Window 类型 | NSPanel | 普通 Window | 不在范围（ADR 0002 §3.1） |
| level / float | PanelLevel::Floating + nonactivating | alwaysOnTop | — |
| collection behavior | fullScreenAuxiliary + canJoinAllSpaces | OS 默认（alwaysOnTop 天然 fullscreen 浮层）| — |
| activation policy | Accessory（不上 Dock / Cmd+Tab） | OS 默认 + skipTaskbar | — |
| 整屏 setBounds | `apply_pet_overlay_fullscreen` cross-platform | 同 | — |
| cursor passthrough | 60Hz Rust polling + setIgnoreCursorEvents | 同 | — |
| PIXI canvas + WebGL fps | 30fps（NSPanel + WKWebView cap，已知）| ~ monitor refresh（Win Chromium WebView2 无 cap，spike 实测 300fps）| — |
| 跨 fullscreen 浮层 | NSPanel + fullScreenAuxiliary 解 | alwaysOnTop 天然支持 | — |

---

## 10. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-15
- **确认时间**：2026-06-15
- **关联需求**：[requirement.md](./requirement.md)
- **关联决策**：[ADR 0004 · 桌宠承载形态与渲染路径](../../decisions/0004-pet-overlay-route/README.md)
- **关联 spike**（ground truth listing 来源）：
  - macOS：[`spike-tauri-nspanel-pet-window.md`](../../explorations/desktop-pet-form-factor/spike-tauri-nspanel-pet-window.md) §5.5
  - Win：[`spike-tauri-win-overlay-research.md`](../../explorations/desktop-pet-form-factor/spike-tauri-win-overlay-research.md) §6.4.4
- **下一步**：本文档确认后撰写同目录 [`progress.md`](./progress.md) 并进入 Phase 3（实施）
