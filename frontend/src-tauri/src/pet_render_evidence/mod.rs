//! Structured, content-free graph of renderer progress and host-window operations.
pub(crate) mod journal;
mod manager;
mod model;
pub(crate) use manager::RenderEvidence;
use model::Sample;
use serde_json::{json, Value};
use tauri::{Manager, Runtime, WebviewWindow};

#[tauri::command]
pub(crate) fn record_pet_render_sample(
    window: WebviewWindow,
    sample: Sample,
) -> Result<bool, String> {
    if window.label() != "pet" {
        return Err("pet-window-required".into());
    }
    Ok(window
        .try_state::<RenderEvidence>()
        .is_some_and(|m| m.sample(sample)))
}
#[tauri::command]
pub(crate) async fn mark_pet_render_incident(app: tauri::AppHandle) -> Result<Value, String> {
    let manager = app
        .try_state::<RenderEvidence>()
        .ok_or("observer-unavailable")?
        .inner()
        .clone();
    let id = manager.mark().await?;
    Ok(json!({"incidentId":id,"persisted":true,"postWindowComplete":false}))
}
#[tauri::command]
pub(crate) fn get_pet_render_evidence_status(app: tauri::AppHandle) -> Value {
    app.try_state::<RenderEvidence>()
        .map(|s| s.snapshot())
        .unwrap_or(json!({"supported":false,"writerStopped":true}))
}

pub(crate) fn record<R: Runtime>(app: &tauri::AppHandle<R>, event: Value) {
    if let Some(m) = app.try_state::<RenderEvidence>() {
        m.record(event);
    }
}

pub(crate) fn window_event(window: &tauri::Window, event: &tauri::WindowEvent) {
    if window.label() != "pet" {
        return;
    }
    let reason = match event {
        tauri::WindowEvent::Resized(_) => "resized",
        tauri::WindowEvent::Moved(_) => "moved",
        tauri::WindowEvent::ScaleFactorChanged { .. } => "scale-changed",
        _ => return,
    };
    if let Some(view) = window.app_handle().get_webview_window("pet") {
        record(
            window.app_handle(),
            json!({"kind":"window-observation","reason":reason,"state":window_state(&view)}),
        );
    }
}
/// Call sites supply only stable action/reason codes, never arbitrary error strings.
pub(crate) fn operation<R: Runtime>(
    window: &WebviewWindow<R>,
    action: &'static str,
    reason: &'static str,
    before: Value,
    ok: bool,
) {
    let after = window_state(window);
    record(
        window.app_handle(),
        json!({"kind":"window-operation","action":action,"reason":reason,
        "outcome":if ok{"ok"}else{"error"},"window":window.label(),"before":before,"after":after}),
    );
}
pub(crate) fn window_state<R: Runtime>(window: &WebviewWindow<R>) -> Value {
    let value = json!({"visible":window.is_visible().ok(),"position":window.outer_position().ok(),
        "size":window.outer_size().ok(),"scale":window.scale_factor().ok()});
    #[cfg(target_os = "windows")]
    {
        use windows::Win32::UI::WindowsAndMessaging::{GetWindowLongPtrW, GWL_EXSTYLE, GWL_STYLE};
        let mut value = value;
        if let Ok(hwnd) = window.hwnd() {
            value["hwnd"] = format!("0x{:x}", hwnd.0 as usize).into();
            value["style"] = unsafe { GetWindowLongPtrW(hwnd, GWL_STYLE) as u32 }.into();
            value["extendedStyle"] = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) as u32 }.into();
        }
        value
    }
    #[cfg(not(target_os = "windows"))]
    value
}
