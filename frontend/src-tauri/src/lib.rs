use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};
use tauri_plugin_opener::OpenerExt;

mod autostart;
mod avatar_models;
mod bubble_window;
mod data_migration;
mod diagnostics;
mod paths;
mod permissions;
mod pet_cursor_passthrough;
mod pet_display_topology;
mod power_policy;
mod presence;
mod push_subscriber;
mod runtime_config;
mod runtime_services;
mod settings;
mod shell_diagnostics;
mod ui_state;
mod window_placement;

const EVENT_WINDOW_SHOWN: &str = "window://shown";
const EVENT_WINDOW_HIDDEN: &str = "window://hidden";
const EVENT_SETTINGS_NAVIGATE: &str = "settings://navigate";
const EVENT_PET_DISPLAY_TOPOLOGY_CHANGED: &str = "pet://display-topology-changed";
const PET_OVERLAY_RECONCILE_INTERVAL_MS: u64 = 1000;
const PET_OVERLAY_BOUNDS_SETTLE_MS: u64 = 1200;

#[derive(Default)]
struct VoiceLaunchState {
    pending_start: Mutex<Option<VoiceStartRequest>>,
}

#[derive(Default)]
struct SettingsNavigationState {
    inner: Mutex<SettingsNavigationStateInner>,
}

#[derive(Default)]
struct SettingsNavigationStateInner {
    next_request_id: u64,
    pending: Option<SettingsNavigationEnvelope>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct SettingsOpenRequest {
    target: Option<String>,
    feedback: Option<SettingsFeedbackContext>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct SettingsFeedbackContext {
    preselect_session_id: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct SettingsNavigationEnvelope {
    request_id: u64,
    request: SettingsOpenRequest,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "lowercase")]
enum PersonaOpenTarget {
    File,
    Folder,
}

impl SettingsNavigationState {
    fn set_pending(
        &self,
        request: SettingsOpenRequest,
    ) -> Result<SettingsNavigationEnvelope, String> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|e| format!("settings navigation state poisoned: {e}"))?;
        inner.next_request_id = inner.next_request_id.saturating_add(1);
        let envelope = SettingsNavigationEnvelope {
            request_id: inner.next_request_id,
            request,
        };
        inner.pending = Some(envelope.clone());
        Ok(envelope)
    }

    fn take_pending(&self) -> Result<Option<SettingsNavigationEnvelope>, String> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|e| format!("settings navigation state poisoned: {e}"))?;
        Ok(inner.pending.take())
    }
}

#[derive(Default)]
struct PetOverlayState {
    inner: Mutex<PetOverlayStateInner>,
}

#[derive(Default)]
struct PetOverlayStateInner {
    topology_epoch: pet_display_topology::TopologyEpochState,
    bounds_correction_request_id: u64,
    bounds_correction_pending: bool,
}

impl VoiceLaunchState {
    fn set_pending_start(&self, req: VoiceStartRequest) -> Result<(), String> {
        let mut pending = self
            .pending_start
            .lock()
            .map_err(|e| format!("voice launch state poisoned: {e}"))?;
        *pending = Some(req);
        Ok(())
    }

    fn take_pending_start(&self) -> Result<Option<VoiceStartRequest>, String> {
        let mut pending = self
            .pending_start
            .lock()
            .map_err(|e| format!("voice launch state poisoned: {e}"))?;
        Ok(pending.take())
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
struct VoiceStartRequest {
    session_id: Option<String>,
    persona: Option<String>,
    model: Option<String>,
    defer_start: Option<bool>,
    trace_id: Option<String>,
}

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

fn read_pet_display_topology(
    window: &WebviewWindow,
) -> Option<pet_display_topology::DisplayTopologySnapshot> {
    let monitors = match window.available_monitors() {
        Ok(m) => m,
        Err(e) => {
            log::warn!("pet overlay reconcile: available_monitors failed: {e}");
            return None;
        }
    };
    let snapshots = monitors
        .iter()
        .map(|monitor| {
            let position = monitor.position();
            let size = monitor.size();
            pet_display_topology::MonitorSnapshot {
                x: position.x,
                y: position.y,
                width: size.width,
                height: size.height,
                scale_milli: pet_display_topology::scale_to_milli(monitor.scale_factor()),
            }
        })
        .collect();
    let snapshot = pet_display_topology::display_topology_snapshot(snapshots);
    if snapshot.is_none() {
        log::warn!("pet overlay reconcile: no usable monitors found");
    }
    snapshot
}

fn pet_overlay_bounds_match(
    window: &WebviewWindow,
    bounds: pet_display_topology::OverlayBounds,
) -> bool {
    let Ok(position) = window.outer_position() else {
        return false;
    };
    let Ok(size) = window.outer_size() else {
        return false;
    };
    position.x == bounds.x
        && position.y == bounds.y
        && size.width == bounds.width
        && size.height == bounds.height
}

fn resolve_pet_overlay_reconcile_decision(
    app: &tauri::AppHandle,
    snapshot: &pet_display_topology::DisplayTopologySnapshot,
    force: bool,
) -> pet_display_topology::TopologyEpochDecision {
    app.try_state::<PetOverlayState>()
        .and_then(|state| {
            state
                .inner
                .lock()
                .ok()
                .map(|mut guard| guard.topology_epoch.accept(snapshot, force))
        })
        .unwrap_or(pet_display_topology::TopologyEpochDecision {
            topology_changed: force,
            generation: None,
            previous_topology: None,
        })
}

fn schedule_pet_overlay_bounds_correction(
    app: &tauri::AppHandle,
    reason: &'static str,
    reset_pending: bool,
) {
    let Some(request_id) = app.try_state::<PetOverlayState>().and_then(|state| {
        state.inner.lock().ok().and_then(|mut guard| {
            if guard.bounds_correction_pending && !reset_pending {
                return None;
            }
            guard.bounds_correction_request_id =
                guard.bounds_correction_request_id.saturating_add(1);
            guard.bounds_correction_pending = true;
            Some(guard.bounds_correction_request_id)
        })
    }) else {
        return;
    };

    log::info!(
        "pet overlay bounds correction scheduled: reason={} request_id={} settle_ms={}",
        reason,
        request_id,
        PET_OVERLAY_BOUNDS_SETTLE_MS
    );
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(PET_OVERLAY_BOUNDS_SETTLE_MS)).await;
        let should_run = handle
            .try_state::<PetOverlayState>()
            .and_then(|state| {
                state.inner.lock().ok().map(|mut guard| {
                    if guard.bounds_correction_request_id == request_id {
                        guard.bounds_correction_pending = false;
                        true
                    } else {
                        false
                    }
                })
            })
            .unwrap_or(false);
        if should_run {
            reconcile_pet_overlay(&handle, "bounds-settled", false);
        }
    });
}

/// 017 / display topology · 把 pet 窗撑成所有显示器并集，并在运行期持续校准。
///
/// 这不是一次性 setup 动作：外接屏拔插、分辨率变化、DPI scale 变化、主副屏布局变化
/// 都会改变 pet overlay 的真实坐标系。Rust 侧持有 monitor topology signature，
/// 发现变化或窗口 bounds 被系统改写时重新 set_position/set_size，并通知前端刷新
/// monitor viewports。
fn reconcile_pet_overlay(app: &tauri::AppHandle, reason: &'static str, force: bool) {
    let Some(pet) = app.get_webview_window("pet") else {
        return;
    };
    let Some(snapshot) = read_pet_display_topology(&pet) else {
        return;
    };
    let bounds = snapshot.bounds;
    let decision = resolve_pet_overlay_reconcile_decision(app, &snapshot, force);
    let previous_bounds = decision
        .previous_topology
        .as_ref()
        .map(|topology| topology.bounds);
    let previous_monitors = decision
        .previous_topology
        .as_ref()
        .map(|topology| topology.monitors.clone());
    let bounds_changed = !pet_overlay_bounds_match(&pet, bounds);

    if !decision.topology_changed && !bounds_changed {
        return;
    }
    if !decision.topology_changed && bounds_changed && reason != "bounds-settled" {
        schedule_pet_overlay_bounds_correction(app, reason, reason != "poll");
        return;
    }

    let generation_label = decision
        .generation
        .map(|generation| generation.to_string())
        .unwrap_or_else(|| "-".to_string());
    log::info!(
        "pet overlay reconcile: reason={} generation={} topology_changed={} bounds_changed={} monitors={} union=({},{}) {}x{}",
        reason,
        generation_label,
        decision.topology_changed,
        bounds_changed,
        snapshot.monitors.len(),
        bounds.x,
        bounds.y,
        bounds.width,
        bounds.height
    );
    if let Err(e) = pet.set_position(tauri::Position::Physical(tauri::PhysicalPosition::new(
        bounds.x, bounds.y,
    ))) {
        log::warn!("pet overlay reconcile: set_position failed: {e}");
    }
    if let Err(e) = pet.set_size(tauri::Size::Physical(tauri::PhysicalSize::new(
        bounds.width,
        bounds.height,
    ))) {
        log::warn!("pet overlay reconcile: set_size failed: {e}");
    }

    let Some(generation) = decision.generation else {
        return;
    };

    let payload = pet_display_topology::DisplayTopologyChangedPayload {
        reason: reason.to_string(),
        generation,
        bounds,
        previous_bounds,
        previous_monitors,
        monitors: snapshot.monitors,
    };
    if let Err(e) = app.emit_to("pet", EVENT_PET_DISPLAY_TOPOLOGY_CHANGED, payload) {
        log::warn!("pet overlay reconcile: emit topology changed failed: {e}");
    }
}

fn spawn_pet_overlay_reconciler(app: &tauri::AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_millis(PET_OVERLAY_RECONCILE_INTERVAL_MS)).await;
            reconcile_pet_overlay(&handle, "poll", false);
        }
    });
}

fn is_loopback_dev_host(host: Option<&str>) -> bool {
    matches!(host, Some("localhost" | "127.0.0.1" | "::1"))
}

fn is_tauri_internal_host(host: Option<&str>) -> bool {
    matches!(host, Some("tauri.localhost"))
}

fn is_allowed_app_navigation(url: &tauri::Url) -> bool {
    match url.scheme() {
        "tauri" | "asset" | "about" => true,
        // Tauri 2 serves bundled assets through this internal HTTP(S) origin on Windows.
        // It must remain allowed in release builds; arbitrary localhost/external HTTP stays blocked.
        "http" | "https" if is_tauri_internal_host(url.host_str()) => true,
        "http" | "https" if cfg!(debug_assertions) => is_loopback_dev_host(url.host_str()),
        _ => false,
    }
}

fn navigation_guard<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    tauri::plugin::Builder::new("navigation-guard")
        .on_navigation(|webview, url| {
            let allowed = is_allowed_app_navigation(url);
            if !allowed {
                log::warn!(
                    "blocked external webview navigation: window={} url={}",
                    webview.label(),
                    url
                );
            }
            allowed
        })
        .build()
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

#[derive(Debug, PartialEq, Eq)]
struct WindowRevealPlan {
    place_before_show: bool,
    unminimize: bool,
}

fn plan_window_reveal(is_visible: bool, is_minimized: bool) -> WindowRevealPlan {
    WindowRevealPlan {
        place_before_show: !is_visible,
        unminimize: is_minimized,
    }
}

/// 恢复、显示并聚焦指定 label 的 webview window（chat / settings 等复用）。
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
            let is_visible = w.is_visible().unwrap_or_else(|e| {
                log::warn!("show_and_focus: window={label} read visible state failed: {e}");
                false
            });
            let is_minimized = w.is_minimized().unwrap_or_else(|e| {
                log::warn!("show_and_focus: window={label} read minimized state failed: {e}");
                false
            });
            let reveal_plan = plan_window_reveal(is_visible, is_minimized);

            // visible 与 minimized 是两个独立状态。Windows 下 show/set_focus 不会恢复一个
            // 已 visible 的最小化窗口，必须先显式 unminimize，才能让后续 focus 生效。
            if reveal_plan.unminimize {
                match w.unminimize() {
                    Ok(()) => log::info!("show_and_focus: window={label} unminimize ok"),
                    Err(e) => {
                        log::warn!("show_and_focus: window={label} unminimize failed: {e}")
                    }
                }
            }
            if reveal_plan.place_before_show {
                if let Err(e) = window_placement::place_window_before_show(&app_clone, &w) {
                    log::warn!("show_and_focus: window={label} placement failed: {e}");
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

/// 打开设置并投递一次性语义定位请求；无请求时保持兼容，仅打开窗口。
#[tauri::command]
fn open_settings(
    app: tauri::AppHandle,
    state: tauri::State<'_, SettingsNavigationState>,
    request: Option<SettingsOpenRequest>,
) -> Result<(), String> {
    let envelope = request
        .map(|request| state.set_pending(request))
        .transpose()?;
    show_and_focus(&app, "settings")?;
    if let Some(envelope) = envelope {
        app.emit_to("settings", EVENT_SETTINGS_NAVIGATE, envelope)
            .map_err(|e| format!("emit settings navigation failed: {e}"))?;
    }
    Ok(())
}

pub(crate) fn show_permission_settings(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app.state::<SettingsNavigationState>();
    let envelope = state.set_pending(SettingsOpenRequest {
        target: Some("permissions".to_string()),
        feedback: None,
    })?;
    show_and_focus(app, "settings")?;
    app.emit_to("settings", EVENT_SETTINGS_NAVIGATE, envelope)
        .map_err(|error| format!("emit permission settings navigation failed: {error}"))
}

#[tauri::command]
fn consume_pending_settings_navigation(
    state: tauri::State<'_, SettingsNavigationState>,
) -> Result<Option<SettingsNavigationEnvelope>, String> {
    state.take_pending()
}

/// 050：只打开应用已知的人格文件或目录，不接受前端传入任意路径。
#[tauri::command]
fn open_persona_target(app: tauri::AppHandle, target: PersonaOpenTarget) -> Result<(), String> {
    let persona_dir = paths::editable_persona_dir();
    let persona_file = paths::editable_persona_path();
    if !persona_file.is_file() {
        return Err("persona.md 尚未准备好，请稍后重试".to_string());
    }

    let canonical_dir = persona_dir
        .canonicalize()
        .map_err(|e| format!("resolve persona directory failed: {e}"))?;
    let canonical_file = persona_file
        .canonicalize()
        .map_err(|e| format!("resolve persona file failed: {e}"))?;
    if !canonical_file.starts_with(&canonical_dir) {
        return Err("persona file resolved outside the expected directory".to_string());
    }

    let path = match target {
        PersonaOpenTarget::File => canonical_file,
        PersonaOpenTarget::Folder => canonical_dir,
    };
    app.opener()
        .open_path(path.to_string_lossy().into_owned(), None::<&str>)
        .map_err(|e| format!("open persona target failed: {e}"))
}

/// 022 · 桌宠 ActionBar "接入 IM" 按钮调用。
#[tauri::command]
fn open_im_connect(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "im-connect")
}

/// 029 · 打开独立语音通话小窗。
#[tauri::command]
fn open_voice_call(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "voice-call")
}

#[tauri::command]
fn open_voice_call_with_start(
    app: tauri::AppHandle,
    state: tauri::State<'_, VoiceLaunchState>,
    req: Option<VoiceStartRequest>,
) -> Result<(), String> {
    if let Some(req) = req {
        state.set_pending_start(req)?;
    }
    show_and_focus(&app, "voice-call")
}

#[tauri::command]
fn consume_pending_voice_start(
    state: tauri::State<'_, VoiceLaunchState>,
) -> Result<Option<VoiceStartRequest>, String> {
    state.take_pending_start()
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

/// 完全隐藏桌宠窗口的兜底 command。041 之后 ActionBar 主交互改为前端
/// 前端 dock state，贴边召回不再调用此命令。
///
/// **唤回路径**：系统托盘菜单 "显示/隐藏桌宠"（`setup_tray` 内 `toggle_pet`
/// menu event handler）。tray toggle 只控制窗口 show/hide，不改桌宠尺寸偏好。
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
        if (*g - dpr).abs() < f64::EPSILON {
            return;
        }
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
    let context = tauri::generate_context!();
    let resource_dir =
        tauri::utils::platform::resource_dir(context.package_info(), &tauri::Env::default()).ok();
    // 028 · 启动期同步读盘，拼 js_init_script；所有 webview 加载前注入，保证首帧主题正确。
    let bootstrap_settings = settings::load_from_disk_or_default();
    let init_script = settings::build_init_script(&bootstrap_settings);
    let settings_state = Arc::new(Mutex::new(bootstrap_settings));
    let runtime_service_supervisor =
        Arc::new(runtime_services::RuntimeServiceSupervisor::from_environment(resource_dir));
    let migration_coordinator = Arc::new(data_migration::MigrationCoordinator::default());
    let avatar_model_manager = Arc::new(avatar_models::AvatarModelManager::new());
    let shell_diagnostics_manager = shell_diagnostics::create_manager();
    let setup_avatar_model_manager = avatar_model_manager.clone();
    let setup_runtime_service_supervisor = runtime_service_supervisor.clone();
    let setup_settings_state = settings_state.clone();
    let setup_shell_diagnostics_manager = shell_diagnostics_manager.clone();
    let exit_runtime_service_supervisor = runtime_service_supervisor.clone();

    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(bubble_window::BubbleState::default())
        .manage(PetOverlayState::default())
        .manage(PetWebviewState::default())
        .manage(VoiceLaunchState::default())
        .manage(SettingsNavigationState::default())
        .manage(window_placement::WindowPlacementState::default())
        .manage(shell_diagnostics_manager)
        .manage(settings_state.clone())
        .manage(runtime_service_supervisor.clone())
        .manage(migration_coordinator)
        .manage(avatar_model_manager)
        .plugin(navigation_guard())
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .app_name("agent-friend")
                .arg("--autostart")
                .build(),
        )
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
        consume_pending_settings_navigation,
        open_persona_target,
        diagnostics::generate_diagnostic_bundle,
        data_migration::coordinator::get_migration_inventory,
        data_migration::coordinator::export_migration_package,
        data_migration::coordinator::preflight_migration_package,
        data_migration::coordinator::preflight_latest_migration_backup,
        data_migration::coordinator::commit_migration_package,
        data_migration::coordinator::get_latest_migration_backup,
        data_migration::coordinator::delete_latest_migration_backup,
        data_migration::coordinator::get_last_migration_result,
        data_migration::coordinator::clear_last_migration_result,
        open_im_connect,
        open_voice_call,
        open_voice_call_with_start,
        consume_pending_voice_start,
        open_memory_inspector,
        open_live2d_debugger,
        hide_pet,
        pet_cursor_passthrough::set_pet_cursor_passthrough,
        set_pet_webview_dpr,
        settings::get_setting,
        settings::set_setting,
        runtime_config::get_runtime_config_status,
        runtime_config::set_runtime_secret,
        runtime_config::delete_runtime_secret,
        runtime_services::get_runtime_service_snapshot,
        runtime_services::start_runtime_service,
        runtime_services::stop_runtime_service,
        runtime_services::restart_runtime_service,
        runtime_services::prepare_voice_runtime,
        runtime_services::stop_voice_tunnel,
        avatar_models::get_installed_avatar_model_resources,
        avatar_models::get_user_avatar_model_scan,
        avatar_models::rescan_user_avatar_models,
        avatar_models::open_user_spine38_models_directory,
        avatar_models::complete_user_avatar_model_validation,
        autostart::get_autostart_status,
        autostart::set_autostart_enabled,
        ui_state::get_chat_ui_persistence,
        ui_state::get_pet_ui_persistence,
        ui_state::set_last_chat_session_id,
        ui_state::set_pet_monitor_anchor,
        ui_state::set_pet_facing,
        permissions::get_permission_snapshot,
        permissions::refresh_permissions,
        permissions::request_permission_flow,
        permissions::begin_permission_attempt,
        permissions::complete_permission_attempt,
        permissions::defer_permission_flow,
        permissions::open_permission_system_settings,
        permissions::probe_permission_platform,
        power_policy::get_power_policy_snapshot,
        power_policy::refresh_power_policy,
        power_policy::open_power_settings,
        presence::desktop_presence_probe,
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
        consume_pending_settings_navigation,
        data_migration::coordinator::get_migration_inventory,
        data_migration::coordinator::export_migration_package,
        data_migration::coordinator::preflight_migration_package,
        data_migration::coordinator::preflight_latest_migration_backup,
        data_migration::coordinator::commit_migration_package,
        data_migration::coordinator::get_latest_migration_backup,
        data_migration::coordinator::delete_latest_migration_backup,
        data_migration::coordinator::get_last_migration_result,
        data_migration::coordinator::clear_last_migration_result,
        open_persona_target,
        diagnostics::generate_diagnostic_bundle,
        open_im_connect,
        open_voice_call,
        open_voice_call_with_start,
        consume_pending_voice_start,
        hide_pet,
        pet_cursor_passthrough::set_pet_cursor_passthrough,
        set_pet_webview_dpr,
        settings::get_setting,
        settings::set_setting,
        runtime_config::get_runtime_config_status,
        runtime_config::set_runtime_secret,
        runtime_config::delete_runtime_secret,
        runtime_services::get_runtime_service_snapshot,
        runtime_services::start_runtime_service,
        runtime_services::stop_runtime_service,
        runtime_services::restart_runtime_service,
        runtime_services::prepare_voice_runtime,
        runtime_services::stop_voice_tunnel,
        avatar_models::get_installed_avatar_model_resources,
        avatar_models::get_user_avatar_model_scan,
        avatar_models::rescan_user_avatar_models,
        avatar_models::open_user_spine38_models_directory,
        avatar_models::complete_user_avatar_model_validation,
        autostart::get_autostart_status,
        autostart::set_autostart_enabled,
        ui_state::get_chat_ui_persistence,
        ui_state::get_pet_ui_persistence,
        ui_state::set_last_chat_session_id,
        ui_state::set_pet_monitor_anchor,
        ui_state::set_pet_facing,
        permissions::get_permission_snapshot,
        permissions::refresh_permissions,
        permissions::request_permission_flow,
        permissions::begin_permission_attempt,
        permissions::complete_permission_attempt,
        permissions::defer_permission_flow,
        permissions::open_permission_system_settings,
        power_policy::get_power_policy_snapshot,
        power_policy::refresh_power_policy,
        power_policy::open_power_settings,
        presence::desktop_presence_probe,
        bubble_window::show_bubble,
        bubble_window::hide_bubble,
        bubble_window::set_bubble_size,
        bubble_window::update_sprite_pos,
    ]);

    builder
        .on_window_event(|window, event| {
            if let Some(manager) = window
                .app_handle()
                .try_state::<Arc<shell_diagnostics::ShellDiagnosticsManager>>()
            {
                manager.record_tauri_window_event(window, event);
            }
            if matches!(event, tauri::WindowEvent::Focused(true)) {
                permissions::schedule_refresh(
                    window.app_handle(),
                    permissions::manager::PermissionRefreshReason::Focused,
                );
            }
            if window.label() == "pet" {
                match event {
                    tauri::WindowEvent::Resized(_) => {
                        let app = window.app_handle().clone();
                        tauri::async_runtime::spawn(async move {
                            tokio::time::sleep(Duration::from_millis(100)).await;
                            reconcile_pet_overlay(&app, "pet-resized", false);
                        });
                    }
                    tauri::WindowEvent::ScaleFactorChanged { .. } => {
                        let app = window.app_handle().clone();
                        tauri::async_runtime::spawn(async move {
                            tokio::time::sleep(Duration::from_millis(100)).await;
                            reconcile_pet_overlay(&app, "pet-scale-factor-changed", false);
                        });
                    }
                    _ => {}
                }
            }
            // 对话窗 / 设置窗 / IM 窗 / dev 工具窗按需显隐：点关闭只隐藏不销毁，便于再次打开。
            // 019 · settings 窗加入复用 chat 同款"关闭即隐藏"语义。
            // 026 · memory-inspector 窗同语义。
            if window_placement::is_managed_window_label(window.label()) {
                if matches!(
                    event,
                    tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_)
                ) {
                    if let Some(webview_window) =
                        window.app_handle().get_webview_window(window.label())
                    {
                        if let Err(e) = window_placement::capture_window_placement(&webview_window)
                        {
                            log::warn!(
                                "window placement: capture failed for {}: {e}",
                                window.label()
                            );
                        }
                    }
                }
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    if let Some(webview_window) =
                        window.app_handle().get_webview_window(window.label())
                    {
                        if let Err(e) = window_placement::remember_window_placement(&webview_window)
                        {
                            log::warn!(
                                "window placement: remember failed for {}: {e}",
                                window.label()
                            );
                        }
                    }
                    if window.label() == "im-connect" {
                        if let Err(e) = window.emit(EVENT_WINDOW_HIDDEN, ()) {
                            log::warn!("im-connect emit hidden failed: {e}");
                        }
                    }
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
                                ts,
                                record.level(),
                                component,
                                message
                            ))
                        },
                    )
                    .build(),
            )?;

            if let Err(error) = setup_shell_diagnostics_manager.start(app.handle()) {
                log::warn!("windows shell diagnostics observer started partially: {error}");
            }

            // 028 · logger attach 后再补一次 bootstrap 结果，方便 R3 路径/主题排查。
            let bootstrap_summary = settings_state
                .lock()
                .map(|s| {
                    format!(
                        "theme={} avatarModelId={} petAvatarSize={}",
                        s.theme_attr(),
                        s.avatar_model_id,
                        s.pet_avatar_size.attr()
                    )
                })
                .unwrap_or_else(|_| "<poisoned>".to_string());
            log::info!("settings: bootstrap state loaded; {bootstrap_summary}");
            log::info!(
                "avatar models: assets_dir={:?} catalog_path={:?}",
                paths::avatar_model_assets_dir(),
                paths::avatar_model_catalog_path()
            );
            app.asset_protocol_scope()
                .allow_directory(paths::avatar_model_assets_dir(), true)
                .map_err(|error| {
                    std::io::Error::new(
                        std::io::ErrorKind::Other,
                        format!("avatar model asset scope: {error}"),
                    )
                })?;
            permissions::init(app.handle()).map_err(|error| {
                std::io::Error::new(
                    std::io::ErrorKind::Other,
                    format!("permissions init failed: {error}"),
                )
            })?;
            let power_policy_manager = power_policy::create_manager(
                app.handle().clone(),
                setup_settings_state.clone(),
                setup_runtime_service_supervisor.clone(),
            );
            app.manage(power_policy_manager.clone());
            power_policy_manager.start(app.handle().clone());
            avatar_models::ensure_builtin_avatar_models_installed(app.handle()).map_err(|e| {
                std::io::Error::new(std::io::ErrorKind::Other, format!("avatar models: {e}"))
            })?;
            setup_avatar_model_manager
                .start(app.handle().clone())
                .map_err(|e| {
                    std::io::Error::new(
                        std::io::ErrorKind::Other,
                        format!("avatar model watcher: {e}"),
                    )
                })?;

            setup_tray(app.handle())?;
            spawn_cursor_feed(app.handle());
            // 015 · 启动 push channel 长 SSE 订阅；事件经 emit_to("pet", "agent://push", ...) 透传到 pet webview
            push_subscriber::spawn_push_subscriber(app.handle(), bridge_base_url());
            let handle = app.handle().clone();
            let supervisor = setup_runtime_service_supervisor.clone();
            let recovery_settings = setup_settings_state.clone();
            tauri::async_runtime::spawn(async move {
                match data_migration::coordinator::recover_incomplete_transaction(
                    &handle,
                    &supervisor,
                    &recovery_settings,
                )
                .await
                {
                    Ok(true) => log::warn!("migration: recovered an incomplete import"),
                    Ok(false) => {}
                    Err(error) => {
                        log::error!("migration recovery failed; runtime startup blocked: {error}");
                        return;
                    }
                }
                if let Err(error) = supervisor
                    .start(Some(&handle), runtime_services::RuntimeServiceKind::Bridge)
                    .await
                {
                    log::error!("bridge supervisor startup failed: {error}");
                }
                let voice_mode = std::env::var("AGENT_FRIEND_DEV_VOICE_MODE")
                    .unwrap_or_else(|_| "local".to_string());
                if voice_mode != "off" {
                    if let Err(error) = supervisor
                        .start(Some(&handle), runtime_services::RuntimeServiceKind::Voice)
                        .await
                    {
                        log::error!("voice supervisor startup failed: {error}");
                    }
                }
            });
            // 017 · pet 窗形态切换为整屏 transparent overlay
            //   - cross-platform · monitors union setBounds（先于 to_panel）
            //   - macOS only · ActivationPolicy::Accessory + NSPanel 转换（沿
            //     macOS spike §5.5.5 关键顺序：setBounds → policy → to_panel）
            //   - Win / Linux · tauri.conf.json 已配 alwaysOnTop + skipTaskbar +
            //     transparent；Win spike §6.4.3 实证 alwaysOnTop 天然支持 fullscreen 浮层
            //   - 015 → 016 的 apply_floating_window_level 在 pet 上不再调用（NSPanel 路径
            //     已等价覆盖 setLevel + collectionBehavior）；helper 本身保留供 bubble_window::init 用
            if let Some(pet) = app.get_webview_window("pet") {
                // Native fail-open must be installed before the transparent window spans the
                // desktop. React later enables hit regions for the avatar/action bar. If the
                // webview is slow or fails to mount, the invisible overlay must never eat all
                // desktop clicks.
                if let Err(error) = pet.set_ignore_cursor_events(true) {
                    log::error!("pet overlay startup fail-open failed: {error}");
                }
                reconcile_pet_overlay(app.handle(), "setup", true);
                if let Err(error) = pet.show() {
                    log::warn!("pet overlay startup show failed: {error}");
                }
                #[cfg(target_os = "macos")]
                {
                    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
                    apply_pet_nspanel(&pet);
                }
            }
            spawn_pet_overlay_reconciler(app.handle());
            #[cfg(debug_assertions)]
            setup_live2d_debugger_window(app.handle())?;
            #[cfg(debug_assertions)]
            permissions::spawn_platform_probe(app.handle().clone());
            // 016 · M16.3 透明窗 workaround + M16.4 跟随轮询 spawn；M16.5 内部对 bubble 窗也调 apply_floating_window_level
            bubble_window::init(app.handle())?;
            Ok(())
        })
        .build(context)
        .expect("error while building tauri application")
        .run(move |app_handle, event| {
            if matches!(event, tauri::RunEvent::Resumed) {
                permissions::schedule_refresh(
                    app_handle,
                    permissions::manager::PermissionRefreshReason::Resumed,
                );
                if let Some(manager) =
                    app_handle.try_state::<Arc<power_policy::PowerPolicyManager>>()
                {
                    manager.request_refresh();
                }
            }
            if matches!(event, tauri::RunEvent::Exit) {
                if let Some(manager) =
                    app_handle.try_state::<Arc<shell_diagnostics::ShellDiagnosticsManager>>()
                {
                    manager.shutdown();
                }
                for window in app_handle.webview_windows().into_values() {
                    if let Err(e) = window_placement::capture_window_placement(&window) {
                        log::warn!(
                            "window placement: exit capture failed for {}: {e}",
                            window.label()
                        );
                    }
                }
                if let Err(e) = window_placement::persist_window_placements(app_handle) {
                    log::warn!("window placement: exit persist failed: {e}");
                }
                tauri::async_runtime::block_on(async {
                    if let Some(manager) =
                        app_handle.try_state::<Arc<power_policy::PowerPolicyManager>>()
                    {
                        manager.shutdown().await;
                    }
                    exit_runtime_service_supervisor
                        .cleanup_owned_children()
                        .await;
                });
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn voice_launch_state_consumes_pending_start_once() {
        let state = VoiceLaunchState::default();
        let req = VoiceStartRequest {
            session_id: Some("session-1".to_string()),
            persona: None,
            model: None,
            defer_start: Some(true),
            trace_id: Some("trace-1".to_string()),
        };

        state
            .set_pending_start(req.clone())
            .expect("set pending start");

        assert_eq!(
            state.take_pending_start().expect("take pending start"),
            Some(req)
        );
        assert_eq!(state.take_pending_start().expect("take again"), None);
    }

    #[test]
    fn settings_navigation_is_ordered_and_consumed_once() {
        let state = SettingsNavigationState::default();
        let first = state
            .set_pending(SettingsOpenRequest {
                target: Some("general".into()),
                feedback: None,
            })
            .expect("set first navigation");
        let second = state
            .set_pending(SettingsOpenRequest {
                target: Some("feedback".into()),
                feedback: Some(SettingsFeedbackContext {
                    preselect_session_id: Some("session-1".into()),
                }),
            })
            .expect("set second navigation");

        assert!(second.request_id > first.request_id);
        assert_eq!(
            state
                .take_pending()
                .expect("take pending")
                .expect("pending envelope")
                .request_id,
            second.request_id
        );
        assert!(state.take_pending().expect("take again").is_none());
    }

    #[test]
    fn window_reveal_plan_handles_visibility_and_minimized_state_independently() {
        assert_eq!(
            plan_window_reveal(false, false),
            WindowRevealPlan {
                place_before_show: true,
                unminimize: false,
            }
        );
        assert_eq!(
            plan_window_reveal(false, true),
            WindowRevealPlan {
                place_before_show: true,
                unminimize: true,
            }
        );
        assert_eq!(
            plan_window_reveal(true, false),
            WindowRevealPlan {
                place_before_show: false,
                unminimize: false,
            }
        );
        assert_eq!(
            plan_window_reveal(true, true),
            WindowRevealPlan {
                place_before_show: false,
                unminimize: true,
            }
        );
    }

    #[test]
    fn navigation_guard_allows_app_and_dev_urls_only() {
        assert!(is_allowed_app_navigation(
            &tauri::Url::parse("tauri://localhost/chat.html").unwrap()
        ));
        assert!(is_allowed_app_navigation(
            &tauri::Url::parse("asset://localhost/avatar.png").unwrap()
        ));
        assert!(is_allowed_app_navigation(
            &tauri::Url::parse("http://tauri.localhost/pet.html").unwrap()
        ));
        assert!(is_allowed_app_navigation(
            &tauri::Url::parse("https://tauri.localhost/settings.html").unwrap()
        ));
        assert!(is_allowed_app_navigation(
            &tauri::Url::parse("http://localhost:1420/chat.html").unwrap()
        ));
        assert!(is_allowed_app_navigation(
            &tauri::Url::parse("http://127.0.0.1:1420/chat.html").unwrap()
        ));
        assert!(!is_allowed_app_navigation(
            &tauri::Url::parse("https://example.com").unwrap()
        ));
        assert!(!is_allowed_app_navigation(
            &tauri::Url::parse("https://tauri.localhost.example.com/pet.html").unwrap()
        ));
    }
}
