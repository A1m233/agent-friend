use serde::Serialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager,
};

mod push_subscriber;

/// 015 · 让 pet 窗悬浮在全屏 app 之上。
///
/// Tauri 2 的 `alwaysOnTop`（对应 NSFloatingWindowLevel = 3）压不过 fullscreen layer；
/// 这里直接调 NSWindow `setLevel:` 提到 NSScreenSaverWindowLevel = 1000。
/// 失败仅 log warn——pet 窗在全屏 app 下会被遮，但 app 仍可启动（不阻断主流程）。
///
/// Windows / Linux 本期不动（requirement R-4.7.3），退化为单纯 alwaysOnTop。
fn apply_pet_window_level(app: &tauri::AppHandle) {
    #[cfg(target_os = "macos")]
    {
        if let Err(e) = mac_apply_pet_window_level(app) {
            log::warn!("apply_pet_window_level failed: {e}");
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
    }
}

#[cfg(target_os = "macos")]
fn mac_apply_pet_window_level(
    app: &tauri::AppHandle,
) -> Result<(), Box<dyn std::error::Error>> {
    use objc2::{msg_send, runtime::AnyObject};
    let pet = app
        .get_webview_window("pet")
        .ok_or("pet window not found")?;
    let ns_window_ptr = pet.ns_window()?;
    if ns_window_ptr.is_null() {
        return Err("ns_window() returned null".into());
    }
    // SAFETY: ns_window_ptr 由 Tauri 提供、生命周期与 pet 窗一致；
    // 这里只读取 NSWindow 实例发送 setLevel: / setCollectionBehavior: 消息，不持有 ownership。
    let ns_window: &AnyObject = unsafe { &*(ns_window_ptr as *const AnyObject) };
    // NSWindowCollectionBehavior bit flags（AppKit）：
    //   CanJoinAllSpaces       = 1 << 0 = 1     —— 跨虚拟桌面跟随（M15.8 验证生效）
    //   FullScreenAuxiliary    = 1 << 8 = 256   —— 同一 app 内 panel 跟随 fullscreen
    // tauri.conf.json 的 visibleOnAllWorkspaces 已取消（避免 Tauri 自己设 collectionBehavior
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
        "pet window: target level=1000 cb=0x{:x}, verified level={} cb=0x{:x}",
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

/// 显示并聚焦对话窗（桌宠操作栏 / 托盘菜单调用）。
#[tauri::command]
fn open_chat(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("chat") {
        w.show().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// 起一个后台线程，用 Tauri 内置 cursor_position 以 ~60fps 把
/// "光标相对桌宠窗内容区的逻辑坐标" 喂给桌宠窗。
///
/// 前端据此做命中判定并切 setIgnoreCursorEvents，实现透明区穿透。
/// 这是穿透 plumbing；命中判定（几何 → 未来 Live2D 的 alpha 采样）在前端的"缝"里替换。
fn spawn_cursor_feed(app: &tauri::AppHandle) {
    let handle = app.clone();
    std::thread::spawn(move || loop {
        if let Some(pet) = handle.get_webview_window("pet") {
            if let (Ok(cursor), Ok(pos), Ok(scale)) =
                (handle.cursor_position(), pet.outer_position(), pet.scale_factor())
            {
                let x = (cursor.x - pos.x as f64) / scale;
                let y = (cursor.y - pos.y as f64) / scale;
                let _ = handle.emit_to("pet", "pet://cursor", CursorPos { x, y });
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(16));
    });
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
                if let Some(w) = app.get_webview_window("chat") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
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
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![open_chat])
        .on_window_event(|window, event| {
            // 对话窗按需显隐：点关闭只隐藏不销毁，便于再次打开。
            if window.label() == "chat" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            setup_tray(app.handle())?;
            spawn_cursor_feed(app.handle());
            // 015 · 启动 push channel 长 SSE 订阅；事件经 emit_to("pet", "agent://push", ...) 透传到 pet webview
            push_subscriber::spawn_push_subscriber(app.handle(), bridge_base_url());
            // 015 · 让 pet 窗悬浮在全屏 app 之上（macOS only；其他平台退化为 alwaysOnTop）
            apply_pet_window_level(app.handle());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
