//! 016 · 桌宠气泡独立窗承载 —— Rust 侧 bubble window 控制模块。
//!
//! 本模块统一持有 bubble window 的建窗 / 显隐 / 位置跟随 / 尺寸调整 / macOS 加料。
//!
//! - 设计取向：window 控制权统一在 Rust 侧，绕开 Tauri JS 侧
//!   `getCurrentWebviewWindow().position()` 在 macOS 返 0,0 的已知 bug
//!   （Tauri #14673）。前端只通过 invoke 表达"显隐意图 + 期望尺寸"。
//! - 跨平台策略：核心动作走 Tauri 跨平台 WebviewWindow API；唯一 macOS 加料
//!   （`setLevel` + `setCollectionBehavior`）走 015 已有的 `apply_pet_window_level`
//!   helper（M16.5 重命名为 `apply_floating_window_level`）。
//! - 关联需求：[`docs/requirements/016-pet-bubble-independent-window/`](../../../docs/requirements/016-pet-bubble-independent-window/)
//!
//! 本文件按里程碑分层落地：
//! - M16.2：模块骨架 + 几何常量 + `compute_bubble_position` 纯函数 + 单测
//! - M16.3：`BubbleState` + invoke 命令（`show_bubble` / `hide_bubble` / `set_bubble_size`）+ `init` 透明窗 workaround
//! - **M16.4（当前）**：跟随轮询 tokio task（50ms tick + `outer_position`；hidden 时 park）
//! - M16.5：build_bubble_window + macOS 加料调用

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{AppHandle, LogicalSize, Manager, PhysicalPosition, WebviewWindow};
use tokio::sync::Notify;

/// bubble window 在 tauri.conf.json / Manager 里的 label。
pub const BUBBLE_LABEL: &str = "bubble";

/// 跟随轮询间隔（毫秒）。
///
/// 16ms ≈ 60Hz，与显示器刷新率对齐，拖拽时视觉无错位感。
/// hidden 时整段 task park（visible_notify），static-period CPU 占用 ~ 0。
/// Hyprnote `tauri-plugin-overlay` 用 50ms，但 M16.9 真跑发现拖拽有卡顿感 → 提到 60Hz。
pub const TICK_MS: u64 = 16;

/// bubble window 与 pet 主窗之间的视觉间距（px）。
pub const MARGIN: i32 = 8;

/// bubble window 尺寸下限（px）。低于此值由 Rust 端 clamp 到 MIN_*。
pub const MIN_W: u32 = 240;
pub const MIN_H: u32 = 64;

/// bubble window 尺寸上限（px）。超过此值由 Rust 端 clamp 到 MAX_*，
/// 前端 PetBubble 内部走滚动条吸收超长内容。
pub const MAX_W: u32 = 360;
pub const MAX_H: u32 = 480;

/// 初始建窗尺寸（与 tauri.conf.json 的 bubble window width / height 对齐）。
/// init 阶段做透明窗 workaround 时 set_size 用这个值。
const INIT_W: f64 = 280.0;
const INIT_H: f64 = 96.0;

/// pet 主窗的位置 + 尺寸（屏幕物理坐标系）。
#[derive(Debug, Clone, Copy)]
pub struct Anchor {
    pub x: i32,
    pub y: i32,
    pub w: u32,
    pub h: u32,
}

/// bubble window 的当前尺寸（用于翻转判定 + 水平居中计算）。
#[derive(Debug, Clone, Copy)]
pub struct BubbleSize {
    pub w: u32,
    pub h: u32,
}

/// 当前显示器的可用矩形（屏幕物理坐标系，左上为原点）。
#[derive(Debug, Clone, Copy)]
pub struct Screen {
    pub x: i32,
    pub y: i32,
    pub w: u32,
    pub h: u32,
}

/// 17a · 前端 PIXI sprite 在屏幕物理坐标系中的位置 + 尺寸。
///
/// 由前端 `update_sprite_pos` invoke 写入；`run_follow_loop` 16ms tick 读它代替
/// 016 的 `pet.outer_position()`（pet 窗 17a 后撑整屏，outer_position = monitors
/// union 起点，对 sprite 跟随无意义）。
///
/// 坐标系 = 屏幕物理坐标系（与 016 `Anchor.x/y` 一致），`compute_bubble_position` 直接消费。
#[derive(Debug, Clone, Copy)]
pub struct SpritePos {
    pub x: i32,
    pub y: i32,
    pub w: u32,
    pub h: u32,
}

/// bubble window 跨 invoke 共享的运行时状态。
///
/// 由 `lib.rs` 在 `Builder::manage` 阶段注册；invoke 命令通过 `tauri::State` 取；
/// 跟随轮询 tokio task 持有 `Clone`（Arc 引用 clone，开销可忽略）后跨 thread / await 共享。
///
/// - `is_visible` —— show / hide 命令更新；跟随轮询 task 读它决定 park / run。
/// - `visible_notify` —— hidden→show 时 `notify_one`，唤醒 park 中的轮询 task。
/// - `sprite_pos`（17a）—— 前端 `update_sprite_pos` invoke 写入；`run_follow_loop`
///   16ms tick 读它代替 016 的 `pet.outer_position()`。None 时 skip 本 tick（race 兜底：
///   PIXI app mount 慢于 setup hook 时，bubble follow loop 早起 tick 读到 None）。
#[derive(Clone)]
pub struct BubbleState {
    pub is_visible: Arc<AtomicBool>,
    pub visible_notify: Arc<Notify>,
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

/// setup 阶段调一次。
///
/// 当前职责：
/// 1. 确认 bubble window 已由 tauri.conf.json 自动建出（log 兜底）。
/// 2. 透明窗渲染 workaround（Tauri #1564）：创建后立刻 `set_size` 一次，
///    避免 macOS 上首次内容到达前 transparent 窗失去 shadow/border。
/// 3. M16.4 · spawn 跟随轮询 tokio task（hidden 时 park、show 时唤醒；50ms tick）。
///
/// M16.5 会在此基础上叠加 `apply_floating_window_level(&bubble)`。
pub fn init(app: &AppHandle) -> tauri::Result<()> {
    match app.get_webview_window(BUBBLE_LABEL) {
        Some(bubble) => {
            // 透明窗 workaround（Tauri #1564）：让 macOS 在首次 layout 后正确画
            // transparent shadow/border —— 即便 size 不变，调用 set_size 也能触发重新布局。
            if let Err(e) = bubble.set_size(LogicalSize::new(INIT_W, INIT_H)) {
                log::warn!("bubble_window::init set_size workaround failed: {e}");
            } else {
                log::info!(
                    "bubble window initialized: transparent workaround set_size({INIT_W}x{INIT_H}) applied"
                );
            }
            // M16.5 · macOS 加料：bubble 窗跟随跨 Space + 浮全屏（复用 015 helper）
            crate::apply_floating_window_level(&bubble);
        }
        None => {
            log::warn!(
                "bubble window not found at setup; check tauri.conf.json windows[] for label=\"{BUBBLE_LABEL}\""
            );
        }
    }

    // M16.4 · 跟随轮询：从 Tauri State 拿出 BubbleState clone（Arc 引用），
    // 跨 tokio task 共享。spawn 之后 task 在 hidden 时 park、show 时唤醒。
    let state = app.state::<BubbleState>().inner().clone();
    spawn_follow_loop(app.clone(), state);

    Ok(())
}

/// 显示 bubble window + 唤醒跟随轮询 task。
///
/// 前端在 `usePetBubbleStore.phase` 从 idle 切到 showing/expanded 时 invoke 调本命令。
/// 多次调用幂等：Tauri `show()` 对已可见窗口 no-op；`is_visible` 写入只是更新内部
/// "应该可见"语义；`notify_one` 对未 park 的 task 也是 no-op。
///
/// **AC-5 不抢焦点**：macOS 上用 NSWindow `orderFrontRegardless` 而不是 Tauri 默认
/// 的 `makeKeyAndOrderFront`，避免抢走当前 key window（如 chat 输入态）。
/// Windows / Linux 上 Tauri `.show()` 默认行为对透明无焦点 window 一般也不抢焦点
/// （未真跑验证；跨平台 spike 留下个需求）。
#[tauri::command]
pub fn show_bubble(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
) -> Result<(), String> {
    let bubble = app
        .get_webview_window(BUBBLE_LABEL)
        .ok_or_else(|| format!("window '{BUBBLE_LABEL}' not found"))?;

    // 默认 .show() 在 macOS 上调 makeKeyAndOrderFront 抢焦点；用 orderFrontRegardless 替代
    #[cfg(target_os = "macos")]
    {
        use objc2::{msg_send, runtime::AnyObject};
        let ns_window_ptr = bubble.ns_window().map_err(|e| e.to_string())?;
        if !ns_window_ptr.is_null() {
            let ns_window: &AnyObject = unsafe { &*(ns_window_ptr as *const AnyObject) };
            unsafe {
                let _: () = msg_send![ns_window, orderFrontRegardless];
            }
        } else {
            bubble.show().map_err(|e| e.to_string())?;
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        bubble.show().map_err(|e| e.to_string())?;
    }

    state.is_visible.store(true, Ordering::Release);
    state.visible_notify.notify_one();
    Ok(())
}

/// 隐藏 bubble window + 标记内部"应该不可见"（M16.4 轮询 task 读到后 park）。
#[tauri::command]
pub fn hide_bubble(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
) -> Result<(), String> {
    let bubble = app
        .get_webview_window(BUBBLE_LABEL)
        .ok_or_else(|| format!("window '{BUBBLE_LABEL}' not found"))?;
    bubble.hide().map_err(|e| e.to_string())?;
    state.is_visible.store(false, Ordering::Release);
    Ok(())
}

/// 把 bubble window 的物理尺寸调整到 `(width, height)`，clamp 到 (MIN_*, MAX_*)。
///
/// 前端 `<PetBubble />` 用 `ResizeObserver` 监测内容尺寸 → debounce 一帧 →
/// invoke 调本命令。超过 MAX_H 时前端内部走滚动条吸收。
#[tauri::command]
pub fn set_bubble_size(app: AppHandle, width: u32, height: u32) -> Result<(), String> {
    let (w, h) = clamp_bubble_size(width, height);
    let bubble = app
        .get_webview_window(BUBBLE_LABEL)
        .ok_or_else(|| format!("window '{BUBBLE_LABEL}' not found"))?;
    bubble
        .set_size(LogicalSize::new(f64::from(w), f64::from(h)))
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 17a · 前端 PIXI sprite world position 上报。
///
/// 触发时机：(1) PIXI app mount 完成首次同步；(2) drag pointermove（无前端节流）；
/// (3) drag pointerup commit。idle 期不发。
///
/// **坐标系约定**：前端发的 `(x, y)` 是 sprite 在 pet content 区（= 整屏 webview viewport）
/// 内的**物理像素偏移**（已乘 devicePixelRatio）。Rust 端补上 `pet.outer_position()`
/// （= monitors union 起点 `(min_x, min_y)`，多屏场景可能 < 0）→ 写入 `sprite_pos` cache，
/// 屏幕物理坐标系语义与 016 `Anchor.x/y` 一致，`compute_bubble_position` 直接消费。
///
/// `(w, h)` 是 sprite bounding box 物理像素（avatar-slot Container `getBounds()` 转 DPR 后）。
///
/// **AC-5 修复 · 即时 apply**：写完 cache 后，如 bubble 当前可见，**同步**算 bubble
/// position + `set_position` 一次，**消除 follow loop 16ms tick 等待**。follow loop
/// 保留兜底（show/hide 状态切换 + 多屏起点变化的偶发漂移）。
///
/// **AC-4 trace**：首次 cache 写入（None → Some）log::info 一次，证明 sprite world position
/// 数据流通；drag 期间后续上报保持静默不刷屏。
#[tauri::command]
pub fn update_sprite_pos(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
    x: i32,
    y: i32,
    w: u32,
    h: u32,
) -> Result<(), String> {
    // 拿 pet 窗 outer_position（17a 后 = monitors union 起点）；
    // 拿不到时退化为 (0, 0)（边缘场景：app 启动早期 / pet 窗暂时不可达）
    let origin = app
        .get_webview_window("pet")
        .and_then(|w| w.outer_position().ok())
        .unwrap_or(PhysicalPosition { x: 0, y: 0 });
    let sp = SpritePos {
        x: origin.x + x,
        y: origin.y + y,
        w,
        h,
    };
    let was_none = {
        let mut guard = state.sprite_pos.lock().map_err(|e| e.to_string())?;
        let was_none = guard.is_none();
        *guard = Some(sp);
        was_none
    };
    if was_none {
        log::info!(
            "update_sprite_pos: first sprite pos cached x={} y={} w={} h={} (后续 drag 上报静默不刷屏)",
            sp.x,
            sp.y,
            sp.w,
            sp.h
        );
    }

    // AC-5 即时 apply：跳过 follow loop 16ms tick 等待
    if state.is_visible.load(Ordering::Acquire) {
        if let (Some(pet), Some(bubble)) = (
            app.get_webview_window("pet"),
            app.get_webview_window(BUBBLE_LABEL),
        ) {
            if let (Ok(bubble_size), Some(screen)) =
                (bubble.outer_size(), current_screen_rect(&pet))
            {
                let anchor = Anchor { x: sp.x, y: sp.y, w: sp.w, h: sp.h };
                let bub = BubbleSize { w: bubble_size.width, h: bubble_size.height };
                let (bx, by) = compute_bubble_position(anchor, bub, screen);
                let _ = bubble.set_position(PhysicalPosition { x: bx, y: by });
            }
        }
    }

    Ok(())
}

/// 016 M16.9 · dev-only：往 bubble webview emit 一条假的 push envelope，
/// 模拟 BedtimeSource 产出，跳过 bridge / 真 LLM，便于纯 GUI 手动验证 AC-3/4/5/7。
///
/// 仅 debug build（`cfg(debug_assertions)`）下导出；release build 该 fn 不存在，
/// frontend 也用 `import.meta.env.DEV` gate 入口按钮——双层确保不进生产。
///
/// 调用方：`frontend/src/pages/pet/App.tsx` 的"inject 测试气泡"按钮（dev only）。
#[cfg(debug_assertions)]
#[tauri::command]
pub fn inject_test_envelope(app: AppHandle, text: String) -> Result<(), String> {
    use std::time::{SystemTime, UNIX_EPOCH};
    use tauri::Emitter;

    let seq = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    let envelope = serde_json::json!({
        "kind": "agent_turn",
        "session_id": "dev-test",
        "seq": seq,
        "source_kind": "cron:bedtime",
        "events": [
            { "type": "text_delta", "text": text }
        ]
    });
    // emit_to bubble webview —— 与真 push channel（push_subscriber.rs）走同一通道
    app.emit_to(BUBBLE_LABEL, "agent://push", envelope)
        .map_err(|e| format!("emit_to failed: {e}"))?;
    log::info!("inject_test_envelope: emit_to bubble {} bytes", text.len());
    Ok(())
}

/// 计算 bubble window 应在的左上角屏幕坐标。
///
/// 规则：
/// - 默认贴 pet 主窗**上方**，水平居中对齐 pet 主窗中线。
/// - 屏顶贴墙（pet 顶部距屏顶 < bubble.h + MARGIN）→ 翻到 pet 下方。
/// - 水平方向：clamp 到 `[screen.x, screen.x + screen.w - bubble.w]`，
///   避免气泡左右越出屏幕边界。若 bubble 比 screen 还宽（极端情况），仅保证 x >= screen.x。
///
/// 翻转策略只看屏顶距离 —— 不考虑屏底翻转回上方的二次翻转，因为视觉上"贴底"
/// 比"翻回上方"更稳定（bubble 在 pet 下方但仍在屏内，不影响读）。
pub fn compute_bubble_position(pet: Anchor, bubble: BubbleSize, screen: Screen) -> (i32, i32) {
    // 水平：居中对齐 pet 中线，再 clamp 到屏幕水平范围
    let pet_cx = pet.x + (pet.w as i32) / 2;
    let raw_x = pet_cx - (bubble.w as i32) / 2;
    let max_x = screen.x + (screen.w as i32) - (bubble.w as i32);
    let x = raw_x.clamp(screen.x, max_x.max(screen.x));

    // 垂直：默认贴上方；屏顶贴墙翻到下方
    let above_y = pet.y - (bubble.h as i32) - MARGIN;
    let below_y = pet.y + (pet.h as i32) + MARGIN;
    let top_limit = screen.y + MARGIN;
    let y = if above_y >= top_limit { above_y } else { below_y };

    (x, y)
}

/// 把 `set_bubble_size` invoke 接收到的尺寸 clamp 到模块定义的 (MIN_W..=MAX_W, MIN_H..=MAX_H)。
pub(crate) fn clamp_bubble_size(w: u32, h: u32) -> (u32, u32) {
    (w.clamp(MIN_W, MAX_W), h.clamp(MIN_H, MAX_H))
}

/// 取当前显示器（pet 主窗所在的）的可用矩形（物理坐标）。
///
/// 失败（拿不到 monitor / 跨多屏边界）→ 返回 None，调用方放弃本 tick 的位置同步。
fn current_screen_rect(window: &WebviewWindow) -> Option<Screen> {
    let monitor = window.current_monitor().ok().flatten()?;
    let pos = monitor.position();
    let size = monitor.size();
    Some(Screen {
        x: pos.x,
        y: pos.y,
        w: size.width,
        h: size.height,
    })
}

/// 跟随轮询的实际 loop —— 暴露给 `spawn_follow_loop` 作为 spawned future。
///
/// 设计要点：
/// - hidden 时 `notified().await` park，static-period 零空转；show 时 invoke 命令调
///   `visible_notify.notify_one()` 唤醒。
/// - visible 时每 `TICK_MS` 取一次 pet 主窗位置 + bubble 当前尺寸 + 屏幕矩形，
///   算出 bubble 应在的左上角坐标，调 `set_position`。
/// - 任何中间环节失败（pet 或 bubble 窗暂时不在 / 取位置失败）→ 跳过本 tick，
///   不打断 loop 也不报错（loop 是 long-running、丢一帧不影响后续）。
async fn run_follow_loop(app: AppHandle, state: BubbleState) {
    loop {
        // hidden 时整段 park，避免 long-running 桌宠 idle 期空转
        if !state.is_visible.load(Ordering::Acquire) {
            state.visible_notify.notified().await;
            continue;
        }

        // 17a · 跟随源切换：从 sprite_pos cache 读 anchor 替代 016 的 pet.outer_position()。
        // None 时 skip 本 tick（race 兜底：PIXI app mount 慢于 setup hook 时早起 tick）。
        let sprite = state.sprite_pos.lock().ok().and_then(|g| *g);
        let Some(sp) = sprite else {
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
                let anchor = Anchor {
                    x: sp.x,
                    y: sp.y,
                    w: sp.w,
                    h: sp.h,
                };
                let bub = BubbleSize {
                    w: bubble_size.width,
                    h: bubble_size.height,
                };
                let (x, y) = compute_bubble_position(anchor, bub, screen);
                let _ = bubble.set_position(PhysicalPosition { x, y });
            }
        }

        tokio::time::sleep(Duration::from_millis(TICK_MS)).await;
    }
}

/// 在 Tauri 的 tokio runtime 上 spawn 跟随轮询 task。
///
/// task 持有 `BubbleState` 的 Arc 引用 clone，跨 await 安全；
/// app 退出时 runtime drop 自然终止 loop。
pub fn spawn_follow_loop(app: AppHandle, state: BubbleState) {
    tauri::async_runtime::spawn(run_follow_loop(app, state));
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 标准屏幕：1920×1080，原点 (0, 0)。
    const SCREEN: Screen = Screen { x: 0, y: 0, w: 1920, h: 1080 };
    /// 默认 bubble 尺寸：280×96（与 tauri.conf.json 初始值一致）。
    const BUBBLE: BubbleSize = BubbleSize { w: 280, h: 96 };
    /// pet 主窗尺寸：240×320。
    const PET_W: u32 = 240;
    const PET_H: u32 = 320;

    #[test]
    fn case1_pet_at_screen_center_bubble_above_and_centered() {
        // pet 在屏幕中央 (840, 380) —— 顶部距屏顶 380px，远大于 bubble.h + MARGIN = 104
        let pet = Anchor { x: 840, y: 380, w: PET_W, h: PET_H };
        let (x, y) = compute_bubble_position(pet, BUBBLE, SCREEN);
        // 水平居中：pet 中线 = 840 + 120 = 960；bubble 左上 x = 960 - 140 = 820
        assert_eq!(x, 820);
        // 垂直贴上方：bubble 底边 = pet.y - MARGIN = 372；bubble 左上 y = 372 - 96 = 276
        assert_eq!(y, 380 - 96 - MARGIN);
    }

    #[test]
    fn case2_pet_at_top_edge_flips_below() {
        // pet 顶部贴墙 (840, 0) —— above_y = 0 - 96 - 8 = -104 < top_limit = 8 → 翻到下方
        let pet = Anchor { x: 840, y: 0, w: PET_W, h: PET_H };
        let (_, y) = compute_bubble_position(pet, BUBBLE, SCREEN);
        assert_eq!(y, 0 + (PET_H as i32) + MARGIN);
    }

    #[test]
    fn case3_pet_at_left_edge_clamps_to_screen_left() {
        // pet 紧贴屏幕左边 (0, 380) —— pet 中线 = 120，raw_x = 120 - 140 = -20 < 0 → clamp 到 0
        let pet = Anchor { x: 0, y: 380, w: PET_W, h: PET_H };
        let (x, _) = compute_bubble_position(pet, BUBBLE, SCREEN);
        assert_eq!(x, 0);
    }

    #[test]
    fn case4_pet_at_right_edge_clamps_to_screen_right() {
        // pet 紧贴屏幕右边 (1680, 380) —— pet 中线 = 1800，raw_x = 1800 - 140 = 1660
        // max_x = 1920 - 280 = 1640；1660 > 1640 → clamp 到 1640
        let pet = Anchor { x: 1680, y: 380, w: PET_W, h: PET_H };
        let (x, _) = compute_bubble_position(pet, BUBBLE, SCREEN);
        assert_eq!(x, 1920 - (BUBBLE.w as i32));
    }

    #[test]
    fn case5_bubble_wider_than_screen_pins_to_screen_left() {
        // 极端：screen 比 bubble 还窄（virtual screen=200×1080，bubble=280×96）
        // max_x = 200 - 280 = -80 < screen.x = 0 → clamp 到 screen.x，保证不越左
        let narrow_screen = Screen { x: 0, y: 0, w: 200, h: 1080 };
        let pet = Anchor { x: 0, y: 380, w: PET_W, h: PET_H };
        let (x, _) = compute_bubble_position(pet, BUBBLE, narrow_screen);
        assert_eq!(x, 0);
    }

    // —— clamp_bubble_size ——
    #[test]
    fn clamp_size_within_range_unchanged() {
        assert_eq!(clamp_bubble_size(280, 200), (280, 200));
    }

    #[test]
    fn clamp_size_below_min_pulled_up() {
        assert_eq!(clamp_bubble_size(100, 32), (MIN_W, MIN_H));
    }

    #[test]
    fn clamp_size_above_max_pulled_down() {
        assert_eq!(clamp_bubble_size(800, 999), (MAX_W, MAX_H));
    }

    // —— 17a · BubbleState.sprite_pos cache ——
    #[test]
    fn sprite_pos_default_none() {
        let state = BubbleState::default();
        let g = state.sprite_pos.lock().expect("lock");
        assert!(g.is_none());
    }

    #[test]
    fn sprite_pos_write_then_read() {
        let state = BubbleState::default();
        {
            let mut g = state.sprite_pos.lock().expect("lock");
            *g = Some(SpritePos { x: 100, y: 200, w: 160, h: 160 });
        }
        let g = state.sprite_pos.lock().expect("lock");
        let sp = g.expect("Some");
        assert_eq!(sp.x, 100);
        assert_eq!(sp.y, 200);
        assert_eq!(sp.w, 160);
        assert_eq!(sp.h, 160);
    }

    #[test]
    fn sprite_pos_overwrite() {
        let state = BubbleState::default();
        {
            let mut g = state.sprite_pos.lock().expect("lock");
            *g = Some(SpritePos { x: 1, y: 2, w: 10, h: 10 });
        }
        {
            let mut g = state.sprite_pos.lock().expect("lock");
            *g = Some(SpritePos { x: 999, y: -50, w: 20, h: 20 });
        }
        let g = state.sprite_pos.lock().expect("lock");
        let sp = g.expect("Some");
        assert_eq!(sp.x, 999);
        assert_eq!(sp.y, -50); // 多屏左屏 x 可能 < 0：sprite_pos 也允许负值
        assert_eq!(sp.w, 20);
    }
}
