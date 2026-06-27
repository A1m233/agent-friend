use serde::Serialize;
use std::sync::{Arc, Mutex};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

mod bubble_window;
mod paths;
mod push_subscriber;
mod settings;
mod ui_state;

const EVENT_WINDOW_SHOWN: &str = "window://shown";

// 0004 / 017 · pet 窗 NSPanel 类型声明（macOS only）。
//
// 配置：
// - can_become_key_window=true：startDragging / 输入仍可正常 focus
// - is_floating_panel=true：标记 floating panel 语义（AppKit 内部判定）
//
// 之所以 cfg gate：tauri_nspanel crate 本身就 cfg(target_os="macos")，宏依赖它的类型；
// non-macOS 平台 pet 窗退化走 alwaysOnTop（tauri.conf.json 已配）+ 整屏 setBounds。
#[cfg(target_os = "macos")]
tauri_nspanel::tauri_panel! {
    panel!(PetPanel {
        config: {
            can_become_key_window: true,
            is_floating_panel: true
        }
    })
}

/// 015 → 016 · 让指定 webview window 悬浮在全屏 app 之上 + 跟随用户跨 Space。
///
/// 015 原本只对 pet 窗调用（函数名 `apply_pet_window_level`）；016 M16.5 重命名为
/// `apply_floating_window_level` 并接收任意 `&WebviewWindow`，让 bubble window 也
/// 复用同一段加料 —— 这是 design.md §3.6 锁定的"唯一 macOS 加料、Rust 端全权设
/// collectionBehavior（conf.json 不写 visibleOnAllWorkspaces）"决策的实现承载。
///
/// Tauri 2 的 `alwaysOnTop`（对应 NSFloatingWindowLevel = 3）压不过 fullscreen layer；
/// 这里直接调 NSWindow `setLevel:` 提到 NSScreenSaverWindowLevel = 1000 + 设
/// `collectionBehavior = CanJoinAllSpaces | FullScreenAuxiliary`。
/// 失败仅 log warn —— window 在全屏 app 下会被遮，但 app 仍可启动（不阻断主流程）。
///
/// Windows / Linux 本期不动（016 requirement R-4.7.2），退化为单纯 alwaysOnTop。
pub(crate) fn apply_floating_window_level(window: &WebviewWindow) {
    #[cfg(target_os = "macos")]
    {
        if let Err(e) = mac_apply_floating_window_level(window) {
            log::warn!(
                "apply_floating_window_level failed for window {:?}: {e}",
                window.label()
            );
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = window;
    }
}

#[cfg(target_os = "macos")]
fn mac_apply_floating_window_level(
    window: &WebviewWindow,
) -> Result<(), Box<dyn std::error::Error>> {
    use objc2::{msg_send, runtime::AnyObject};
    let ns_window_ptr = window.ns_window()?;
    if ns_window_ptr.is_null() {
        return Err("ns_window() returned null".into());
    }
    // SAFETY: ns_window_ptr 由 Tauri 提供、生命周期与 webview window 一致；
    // 这里只读取 NSWindow 实例发送 setLevel: / setCollectionBehavior: 消息，不持有 ownership。
    let ns_window: &AnyObject = unsafe { &*(ns_window_ptr as *const AnyObject) };
    // NSWindowCollectionBehavior bit flags（AppKit）：
    //   CanJoinAllSpaces       = 1 << 0 = 1     —— 跨虚拟桌面跟随（M15.8 验证生效）
    //   FullScreenAuxiliary    = 1 << 8 = 256   —— 同一 app 内 panel 跟随 fullscreen
    // tauri.conf.json 的 visibleOnAllWorkspaces 不写（避免 Tauri 自己设 collectionBehavior
    // 冲突）；Rust 端全权设 level + collectionBehavior。
    //
    // 实测限制（详见 docs/issues/007-macos-fullscreen-overlay-limit）：跨 user Space ✅
    // 通过；macOS 绿色按钮 fullscreen mode 桌宠不在 fullscreen Space 显示——这是
    // NSWindow 的系统级限制（fullscreen 是独立 Space、FullScreenAuxiliary 只对同 app
    // 的 panel 生效），非配置 bug。业界桌宠（NekoAI 等）普遍承认此限制。
    //
    // 注：曾试过加 Stationary（1<<4），会让窗口被"钉"在当前 Space 反而不跟随。
    let target_behavior: usize = 1 | 256;
    unsafe {
        // NSScreenSaverWindowLevel = 1000 —— 压过全屏 layer
        let _: () = msg_send![ns_window, setLevel: 1000_isize];
        let _: () = msg_send![ns_window, setCollectionBehavior: target_behavior];
    }
    // verify：读回真实值，确认 Tauri 没在后续步骤覆盖
    let (verified_level, verified_behavior): (isize, usize) = unsafe {
        (
            msg_send![ns_window, level],
            msg_send![ns_window, collectionBehavior],
        )
    };
    log::info!(
        "window {:?}: target level=1000 cb=0x{:x}, verified level={} cb=0x{:x}",
        window.label(),
        target_behavior,
        verified_level,
        verified_behavior,
    );
    if verified_behavior != target_behavior {
        log::warn!(
            "collectionBehavior 与目标不符（target=0x{:x} actual=0x{:x}）——可能被 Tauri 覆盖；考虑挪到 RunEvent::Ready 重设",
            target_behavior,
            verified_behavior,
        );
    }
    Ok(())
}

/// 0004 / 017 · 把 pet 窗用 tauri-nspanel 转成 NSPanel + nonactivating panel +
/// PanelLevel::Floating + collectionBehavior(fullScreenAuxiliary | canJoinAllSpaces)。
///
/// 这是业界 macOS Tauri overlay 标准方案（详见
/// `docs/explorations/desktop-pet-form-factor/industry-standard-tauri-nspanel.md`）。
/// 失败仅 log warn，不阻断 app 启动。
///
/// **关键 API 注意**（spike-tauri-nspanel-pet-window §5.3 / §5.5.3 已实证）：
/// - `set_level` 接 raw `i64` —— 必须 `.value()` 把 enum 转过去
/// - `set_style_mask` / `set_collection_behavior` 接 `objc2_app_kit::NSWindow*` 类型 —— 必须 `.into()`
/// - `set_hides_on_deactivate` 不存在（nonactivating panel + ActivationPolicy::Accessory 已等价覆盖）
#[cfg(target_os = "macos")]
fn apply_pet_nspanel(window: &WebviewWindow) {
    use tauri_nspanel::{CollectionBehavior, PanelLevel, StyleMask, WebviewWindowExt};
    match window.to_panel::<PetPanel>() {
        Ok(panel) => {
            panel.set_level(PanelLevel::Floating.value());
            panel.set_style_mask(StyleMask::empty().nonactivating_panel().into());
            panel.set_collection_behavior(
                CollectionBehavior::new()
                    .full_screen_auxiliary()
                    .can_join_all_spaces()
                    .into(),
            );
            log::info!(
                "pet window {:?} converted to NSPanel + Floating + nonactivating + (fullScreenAuxiliary | canJoinAllSpaces)",
                window.label()
            );
        }
        Err(e) => {
            log::warn!("to_panel failed for {:?}: {e}", window.label());
        }
    }
}

/// 017 · 把 pet 窗撑成整屏 transparent overlay：算 `available_monitors()` 并集 →
/// `set_position` + `set_size`。沿 macOS spike §5.5.5 + Win spike §6.4.4.2 同款 monitors union 算法。
///
/// **跨平台**实现（无 cfg gate）—— Tauri 的 `available_monitors` / `set_position` /
/// `set_size` 三端原生支持。setup hook 在 macOS 上调本 fn → 再 `apply_pet_nspanel`
/// （setBounds 顺序：必须先 setBounds → 再 to_panel，避免 to_panel 后 size 行为不确定）；
/// Win / Linux 上调本 fn 后无加料（tauri.conf.json 已配 alwaysOnTop + transparent + skipTaskbar）。
///
/// 失败仅 log warn，不阻断 app 启动。
fn apply_pet_overlay_fullscreen(window: &WebviewWindow) {
    let monitors = match window.available_monitors() {
        Ok(m) => m,
        Err(e) => {
            log::warn!("apply_pet_overlay_fullscreen: available_monitors failed: {e}");
            return;
        }
    };
    if monitors.is_empty() {
        log::warn!("apply_pet_overlay_fullscreen: no monitors found");
        return;
    }
    let min_x = monitors.iter().map(|m| m.position().x).min().unwrap_or(0);
    let min_y = monitors.iter().map(|m| m.position().y).min().unwrap_or(0);
    let max_x = monitors
        .iter()
        .map(|m| m.position().x + m.size().width as i32)
        .max()
        .unwrap_or(0);
    let max_y = monitors
        .iter()
        .map(|m| m.position().y + m.size().height as i32)
        .max()
        .unwrap_or(0);
    let union_w = (max_x - min_x).max(1) as u32;
    let union_h = (max_y - min_y).max(1) as u32;
    log::info!(
        "apply_pet_overlay_fullscreen: monitors={} union=({},{}) {}x{}",
        monitors.len(),
        min_x,
        min_y,
        union_w,
        union_h
    );
    if let Err(e) = window.set_position(tauri::Position::Physical(
        tauri::PhysicalPosition::new(min_x, min_y),
    )) {
        log::warn!("apply_pet_overlay_fullscreen: set_position failed: {e}");
    }
    if let Err(e) = window.set_size(tauri::Size::Physical(tauri::PhysicalSize::new(
        union_w, union_h,
    ))) {
        log::warn!("apply_pet_overlay_fullscreen: set_size failed: {e}");
    }
}

/// bridge HTTP base URL。
///
/// 默认 `http://127.0.0.1:18800`（与 `frontend/vite.config.ts` proxy target、
/// `scripts/bridge/run.sh` 监听地址一致）。env `AGENT_FRIEND_BRIDGE_URL` 覆盖留口。
/// 生产打包不在本期范围（参 015 design §4.3 / 010 design）。
fn bridge_base_url() -> String {
    std::env::var("AGENT_FRIEND_BRIDGE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:18800".to_string())
}

/// 光标相对桌宠窗内容区的逻辑坐标（CSS px），喂给前端做命中判定。
#[derive(Clone, Serialize)]
struct CursorPos {
    x: f64,
    y: f64,
}

/// 显示并聚焦指定 label 的 webview window（chat / settings 复用）。
///
/// **18 修 macOS Accessory policy 副作用**：17a `set_activation_policy(Accessory)`
/// 让 pet 不进 Dock，但副作用是 `set_focus()` 不让 app 升级为前台 active app —— 新弹
/// 的 / show 后的窗口拿不到 macOS key window status → 用户点输入框时系统层面键盘
/// 事件不路由给 webview → 可聚焦但打字无反应。
///
/// 直接调 `NSApplication.activateIgnoringOtherApps:true` 让 app 在 Accessory policy 下也
/// 能临时 activate → 目标窗成 key window 收键盘事件。**不切 policy**：临时切 Regular
/// 会触发 macOS window reorder，让目标窗短暂上来后被其他 app 窗口盖住（user
/// 反馈实测撞到）；用 NSApp.activate 直接 activate 保持 17a "pet 不进 Dock" 不变。
///
/// 019 · 由 `show_and_focus_chat` 抽象为通用版（chat / settings 复用同款加料）。
fn show_and_focus(app: &tauri::AppHandle, label: &'static str) -> Result<(), String> {
    let app_clone = app.clone();
    app.run_on_main_thread(move || {
        if let Some(w) = app_clone.get_webview_window(label) {
            log::info!("show_and_focus: window={label} requested");
            #[cfg(target_os = "macos")]
            {
                use objc2::{class, msg_send, runtime::AnyObject};
                // NSApplication.sharedApplication 是 class method；用 raw msg_send 避免引入
                // objc2-app-kit 的 NSApplication binding（feature 开销 + 17a 既有 plumbing 风格一致）。
                let nsapp_class = class!(NSApplication);
                let nsapp: *mut AnyObject = unsafe { msg_send![nsapp_class, sharedApplication] };
                if !nsapp.is_null() {
                    unsafe {
                        let _: () = msg_send![nsapp, activateIgnoringOtherApps: true];
                    }
                }
            }
            match w.show() {
                Ok(()) => log::info!("show_and_focus: window={label} show ok"),
                Err(e) => log::warn!("show_and_focus: window={label} show failed: {e}"),
            }
            match w.set_focus() {
                Ok(()) => log::info!("show_and_focus: window={label} focus ok"),
                Err(e) => log::warn!("show_and_focus: window={label} focus failed: {e}"),
            }
            if let Err(e) = app_clone.emit_to(label, EVENT_WINDOW_SHOWN, ()) {
                log::warn!("show_and_focus: window={label} emit shown failed: {e}");
            }
        } else {
            log::warn!("show_and_focus: window={label} not found");
        }
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_chat(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "chat")
}

/// 019 · 桌宠 ActionBar "打开设置" 按钮调用（参 `open_chat` 同款 show + focus 加料）。
#[tauri::command]
fn open_settings(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "settings")
}

/// 029 · 打开独立语音通话小窗。
#[tauri::command]
fn open_voice_call(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "voice-call")
}

/// 026 · dev 模式入口：打开记忆面板窗口。
#[cfg(debug_assertions)]
#[tauri::command]
fn open_memory_inspector(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "memory-inspector")
}

/// 033 · dev-only Live2D 调试器窗口。
///
/// 只在 debug build 注册 command；窗口在 setup 主线程预建为隐藏窗口，避免 Windows
/// WebView2 从 command 动态创建时卡在 about:blank。
#[cfg(debug_assertions)]
#[tauri::command]
fn open_live2d_debugger(app: tauri::AppHandle) -> Result<(), String> {
    log::info!("open_live2d_debugger: showing live2d-debugger window");
    show_and_focus(&app, "live2d-debugger")
}

#[cfg(debug_assertions)]
fn setup_live2d_debugger_window(app: &tauri::AppHandle) -> tauri::Result<()> {
    if app.get_webview_window("live2d-debugger").is_some() {
        return Ok(());
    }
    WebviewWindowBuilder::new(
        app,
        "live2d-debugger",
        WebviewUrl::App("live2d-debugger.html".into()),
    )
    .title("agent-friend · Live2D 调试器")
    .inner_size(460.0, 680.0)
    .min_inner_size(380.0, 520.0)
    .resizable(true)
    .visible(false)
    .build()?;
    log::info!("live2d-debugger window initialized");
    Ok(())
}

/// 019 · 桌宠 ActionBar "隐藏桌宠" 按钮调用。语义：单向隐藏（不切换）。
///
/// **唤回路径**：本期唯一 = 系统托盘菜单 "显示/隐藏桌宠"（`setup_tray` 内 `toggle_pet`
/// menu event handler）。从桌面直接唤回是 `docs/issues/013-pet-recall-from-desktop/`
/// 跟踪的独立缺口，独立立项。
#[tauri::command]
fn hide_pet(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("pet") {
        let _ = w.hide();
    }
    Ok(())
}

/// 起一个后台线程，用 Tauri 内置 cursor_position 以 ~60fps 把
/// "光标相对桌宠窗内容区的逻辑坐标" 喂给桌宠窗。
///
/// 前端据此做命中判定并切 setIgnoreCursorEvents，实现透明区穿透。
/// 这是穿透 plumbing；命中判定（几何 → 未来 Live2D 的 alpha 采样）在前端的"缝"里替换。
///
/// **18b · webview viewport DPR awareness**（issue 012 真根因修复 v2）：
///
/// 17a baseline 用 `pet.scale_factor()` = pet 窗 owner monitor DPR 固定值。pet 整屏
/// overlay 跨多 monitor mixed DPR 时（实测 Win 主屏 175% + 副屏 125%），cursor 跟
/// webview viewport CSS px 坐标系不一致 → cursor 进不到 sprite 矩形 → 穿透永远 ON。
///
/// commit 78dcd26 v1 改按 cursor 当前所在 monitor 的 scale_factor 算，同屏 work，但
/// 跨屏 drag 时 cursor 数字范围突跳（×1.4）→ spriteScreen 矩形（PIXI viewport CSS px
/// 固定坐标系）跟新 cursor 数字不重合 → drag 中断 + 完全穿透。
///
/// v2 真根因：cursor scale 必须用 **webview viewport 实际 DPR**（固定值，整个 webview
/// 一套）。Tauri Rust 拿不到这个值（`pet.scale_factor()` 返 owner monitor DPR，跟
/// viewport DPR 不一定相等；实测 Win 多屏 overlay 时 viewport DPR ≠ owner DPR）。
/// 让前端 mount 时 `invoke('set_pet_webview_dpr')` 上报 `window.devicePixelRatio`，
/// Rust 用这个固定值算。
///
/// dpr 默认 0（未上报）时兜底 `pet.scale_factor()` —— 启动早期（前端上报前几十毫秒）
/// 跨屏 drag 不可能在这个窗口里发生。
fn spawn_cursor_feed(app: &tauri::AppHandle) {
    let handle = app.clone();
    std::thread::spawn(move || loop {
        if let Some(pet) = handle.get_webview_window("pet") {
            if let (Ok(cursor), Ok(pos), Ok(pet_scale)) = (
                handle.cursor_position(),
                pet.outer_position(),
                pet.scale_factor(),
            ) {
                let scale = handle
                    .try_state::<PetWebviewState>()
                    .and_then(|s| s.dpr.lock().ok().map(|g| *g))
                    .filter(|&v| v > 0.0)
                    .unwrap_or(pet_scale);
                let x = (cursor.x - pos.x as f64) / scale;
                let y = (cursor.y - pos.y as f64) / scale;
                let _ = handle.emit_to("pet", "pet://cursor", CursorPos { x, y });
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(16));
    });
}

/// 18b · webview viewport 实际 DPR state（issue 012 跨屏 drag 修复 v2）。
///
/// 前端 mount 时 `invoke('set_pet_webview_dpr', { dpr: window.devicePixelRatio })` 写入，
/// `spawn_cursor_feed` 60Hz 读用作 cursor scale。0.0 表示前端还没上报（默认 / 启动早期）→
/// fallback `pet.scale_factor()`。
#[derive(Default)]
struct PetWebviewState {
    dpr: Mutex<f64>,
}

/// 前端 mount 时调一次（matchMedia listener 在 DPR 变化时再调）。
#[tauri::command]
fn set_pet_webview_dpr(state: tauri::State<PetWebviewState>, dpr: f64) {
    if let Ok(mut g) = state.dpr.lock() {
        *g = dpr;
        log::info!("set_pet_webview_dpr: {dpr}");
    }
}

/// 注册托盘图标与菜单：打开对话 / 显示隐藏桌宠 / 退出。
fn setup_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let open_chat_i = MenuItem::with_id(app, "open_chat", "打开对话", true, None::<&str>)?;
    let toggle_pet_i = MenuItem::with_id(app, "toggle_pet", "显示/隐藏桌宠", true, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open_chat_i, &toggle_pet_i, &quit_i])?;

    let mut builder = TrayIconBuilder::new()
        .menu(&menu)
        .show_menu_on_left_click(true)
        .tooltip("agent-friend")
        // 菜单栏文字标签，避免纯图标在拥挤/刘海屏下找不到
        .title("🐾");
    match app.default_window_icon() {
        Some(icon) => builder = builder.icon(icon.clone()),
        None => log::warn!("default_window_icon is None; tray shows title only"),
    }

    builder
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open_chat" => {
                let _ = show_and_focus(app, "chat");
            }
            "toggle_pet" => {
                if let Some(w) = app.get_webview_window("pet") {
                    if w.is_visible().unwrap_or(true) {
                        let _ = w.hide();
                    } else {
                        let _ = w.show();
                    }
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    log::info!("tray icon created");
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // 028 · 启动期同步读盘，拼 js_init_script；所有 webview 加载前注入，保证首帧主题正确。
    let bootstrap_settings = settings::load_from_disk_or_default();
    let init_script = settings::build_init_script(&bootstrap_settings);
    let settings_state = Arc::new(Mutex::new(bootstrap_settings));

    let builder = tauri::Builder::default()
        .manage(bubble_window::BubbleState::default())
        .manage(PetWebviewState::default())
        .manage(settings_state.clone())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(settings::init::<tauri::Wry>(init_script));

    // 017 · 装 tauri-nspanel plugin（macOS only）；non-macOS 退化为
    // tauri.conf.json 已配的 alwaysOnTop + transparent + skipTaskbar
    #[cfg(target_os = "macos")]
    let builder = builder.plugin(tauri_nspanel::init());

    // 016 M16.9 · dev build 多注册一个 `inject_test_envelope` 命令，
    // 让 pet 操作栏的"inject 测试气泡"按钮跑通——release build 整个 fn 不存在。
    // 028 · settings command 注册到 app 级 invoke_handler，不走路径 plugin 权限文件。
    #[cfg(debug_assertions)]
    let builder = builder.invoke_handler(tauri::generate_handler![
        open_chat,
        open_settings,
        open_voice_call,
        open_memory_inspector,
        open_live2d_debugger,
        hide_pet,
        set_pet_webview_dpr,
        settings::get_setting,
        settings::set_setting,
        ui_state::get_chat_ui_persistence,
        ui_state::set_last_chat_session_id,
        bubble_window::show_bubble,
        bubble_window::hide_bubble,
        bubble_window::set_bubble_size,
        bubble_window::update_sprite_pos,
        bubble_window::inject_test_envelope,
    ]);
    #[cfg(not(debug_assertions))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        open_chat,
        open_settings,
        open_voice_call,
        hide_pet,
        set_pet_webview_dpr,
        settings::get_setting,
        settings::set_setting,
        ui_state::get_chat_ui_persistence,
        ui_state::set_last_chat_session_id,
        bubble_window::show_bubble,
        bubble_window::hide_bubble,
        bubble_window::set_bubble_size,
        bubble_window::update_sprite_pos,
    ]);

    builder
        .on_window_event(|window, event| {
            // 对话窗 / 设置窗 / dev 工具窗按需显隐：点关闭只隐藏不销毁，便于再次打开。
            // 019 · settings 窗加入复用 chat 同款"关闭即隐藏"语义。
            // 026 · memory-inspector 窗同语义。
            if matches!(
                window.label(),
                "chat" | "settings" | "voice-call" | "memory-inspector" | "live2d-debugger"
            ) {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(move |app| {
            let log_dir = paths::log_dir();
            std::fs::create_dir_all(&log_dir).ok();
            app.handle().plugin(
                tauri_plugin_log::Builder::new()
                    .targets([
                        tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Folder {
                            path: log_dir,
                            file_name: Some("tauri".into()),
                        }),
                        tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Stdout),
                    ])
                    .level(log::LevelFilter::Info)
                    .timezone_strategy(tauri_plugin_log::TimezoneStrategy::UseLocal)
                    .rotation_strategy(tauri_plugin_log::RotationStrategy::KeepAll)
                    .max_file_size(10_000_000)
                    .format(
                        |out: tauri_plugin_log::fern::FormatCallback,
                         message: &std::fmt::Arguments,
                         record: &log::Record| {
                            let ts = chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.3f%:z");
                            let component = record
                                .file()
                                .and_then(|f| {
                                    std::path::Path::new(f)
                                        .file_name()
                                        .and_then(|n| n.to_str())
                                        .filter(|n| {
                                            n.ends_with(".tsx")
                                                || n.ends_with(".ts")
                                                || n.ends_with(".jsx")
                                                || n.ends_with(".js")
                                        })
                                })
                                .unwrap_or_else(|| record.target());
                            out.finish(format_args!(
                                "{} [{:<5}] [{}] {}",
                                ts, record.level(), component, message
                            ))
                        },
                    )
                    .build(),
            )?;

            // 028 · logger attach 后再补一次 bootstrap 结果，方便 R3 路径/主题排查。
            let bootstrap_theme = settings_state
                .lock()
                .map(|s| s.theme_attr().to_string())
                .unwrap_or_else(|_| "<poisoned>".to_string());
            log::info!(
                "settings: bootstrap state loaded; theme={}",
                bootstrap_theme
            );

            setup_tray(app.handle())?;
            spawn_cursor_feed(app.handle());
            // 015 · 启动 push channel 长 SSE 订阅；事件经 emit_to("pet", "agent://push", ...) 透传到 pet webview
            push_subscriber::spawn_push_subscriber(app.handle(), bridge_base_url());
            // 017 · pet 窗形态切换为整屏 transparent overlay
            //   - cross-platform · monitors union setBounds（先于 to_panel）
            //   - macOS only · ActivationPolicy::Accessory + NSPanel 转换（沿
            //     macOS spike §5.5.5 关键顺序：setBounds → policy → to_panel）
            //   - Win / Linux · tauri.conf.json 已配 alwaysOnTop + skipTaskbar +
            //     transparent；Win spike §6.4.3 实证 alwaysOnTop 天然支持 fullscreen 浮层
            //   - 015 → 016 的 apply_floating_window_level 在 pet 上不再调用（NSPanel 路径
            //     已等价覆盖 setLevel + collectionBehavior）；helper 本身保留供 bubble_window::init 用
            if let Some(pet) = app.get_webview_window("pet") {
                apply_pet_overlay_fullscreen(&pet);
                #[cfg(target_os = "macos")]
                {
                    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
                    apply_pet_nspanel(&pet);
                }
            }
            #[cfg(debug_assertions)]
            setup_live2d_debugger_window(app.handle())?;
            // 016 · M16.3 透明窗 workaround + M16.4 跟随轮询 spawn；M16.5 内部对 bubble 窗也调 apply_floating_window_level
            bubble_window::init(app.handle())?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
