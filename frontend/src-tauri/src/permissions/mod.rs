pub(crate) mod flow;
pub(crate) mod history;
pub(crate) mod manager;
pub(crate) mod model;
pub(crate) mod platform;
pub(crate) mod projection;
pub(crate) mod registry;

use std::sync::Arc;

use manager::{PermissionManager, PermissionRefreshReason};
use model::{
    PermissionFlowReason, PermissionFlowRequest, PermissionFlowTicket, PermissionId,
    PermissionSnapshot,
};
use tauri::Manager;

pub(crate) fn init(app: &tauri::AppHandle) -> Result<(), String> {
    let history_path = crate::paths::permission_state_path()
        .ok_or_else(|| "permission config directory is unavailable".to_string())?;
    let manager = Arc::new(PermissionManager::new(
        app.clone(),
        platform::create_adapter(app.clone()),
        history_path,
    ));
    app.manage(manager.clone());
    tauri::async_runtime::spawn(async move {
        // setup 阶段 WebView2 profile 仍可能处在首轮导航前，此时 profile 查询会把
        // 已保存的 allow 暂时看成 default。等窗口完成初始化后再建立启动快照和 flow，
        // 避免首次打开权限页短暂误报“尚未处理”。
        tokio::time::sleep(std::time::Duration::from_millis(750)).await;
        manager.refresh(PermissionRefreshReason::Startup).await;
        manager
            .request_flow(PermissionFlowRequest {
                request_id: "startup".to_string(),
                reason: PermissionFlowReason::Startup,
                permission_ids: Vec::new(),
                source_window: None,
            })
            .await;
    });
    Ok(())
}

pub(crate) fn schedule_refresh(app: &tauri::AppHandle, reason: PermissionRefreshReason) {
    if let Some(manager) = app.try_state::<Arc<PermissionManager>>() {
        let manager = manager.inner().clone();
        tauri::async_runtime::spawn(async move {
            manager.schedule_refresh(reason).await;
        });
    }
}

#[tauri::command]
pub(crate) async fn get_permission_snapshot(
    state: tauri::State<'_, Arc<PermissionManager>>,
) -> Result<PermissionSnapshot, String> {
    Ok(state.snapshot().await)
}

#[tauri::command]
pub(crate) async fn refresh_permissions(
    state: tauri::State<'_, Arc<PermissionManager>>,
    reason: Option<PermissionRefreshReason>,
) -> Result<PermissionSnapshot, String> {
    Ok(state
        .refresh(reason.unwrap_or(PermissionRefreshReason::Manual))
        .await)
}

#[tauri::command]
pub(crate) async fn request_permission_flow(
    state: tauri::State<'_, Arc<PermissionManager>>,
    input: PermissionFlowRequest,
) -> Result<PermissionFlowTicket, String> {
    Ok(state.request_flow(input).await)
}

#[tauri::command(rename_all = "camelCase")]
pub(crate) async fn begin_permission_attempt(
    state: tauri::State<'_, Arc<PermissionManager>>,
    flow_id: String,
    permission_id: PermissionId,
) -> Result<platform::RequestInstruction, String> {
    state.begin_attempt(&flow_id, permission_id).await
}

#[tauri::command(rename_all = "camelCase")]
pub(crate) async fn complete_permission_attempt(
    state: tauri::State<'_, Arc<PermissionManager>>,
    flow_id: String,
    permission_id: PermissionId,
) -> Result<PermissionSnapshot, String> {
    state.complete_attempt(&flow_id, permission_id).await
}

#[tauri::command(rename_all = "camelCase")]
pub(crate) async fn defer_permission_flow(
    state: tauri::State<'_, Arc<PermissionManager>>,
    flow_id: String,
) -> Result<PermissionSnapshot, String> {
    state.defer_flow(&flow_id).await
}

#[tauri::command(rename_all = "camelCase")]
pub(crate) async fn open_permission_system_settings(
    state: tauri::State<'_, Arc<PermissionManager>>,
    permission_id: PermissionId,
) -> Result<(), String> {
    state.open_system_settings(permission_id).await
}

#[cfg(all(debug_assertions, target_os = "windows"))]
use serde::Serialize;

#[cfg(all(debug_assertions, target_os = "windows"))]
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct WindowsWebViewProbeResult {
    label: String,
    profile: Result<platform::windows::WindowsWebViewProfileProbe, String>,
}

#[cfg(all(debug_assertions, target_os = "windows"))]
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionPlatformProbe {
    platform: &'static str,
    os_access: platform::windows::WindowsMicrophoneOsProbe,
    webviews: Vec<WindowsWebViewProbeResult>,
    shared_profile: bool,
}

#[cfg(all(debug_assertions, target_os = "windows"))]
#[tauri::command]
pub(crate) async fn probe_permission_platform(
    app: tauri::AppHandle,
) -> Result<PermissionPlatformProbe, String> {
    let mut windows = app.webview_windows().into_iter().collect::<Vec<_>>();
    windows.sort_by(|(left, _), (right, _)| left.cmp(right));

    let mut webviews = Vec::with_capacity(windows.len());
    for (label, window) in windows {
        webviews.push(WindowsWebViewProbeResult {
            label,
            profile: platform::windows::probe_webview_profile(&window).await,
        });
    }

    let successful_paths = webviews
        .iter()
        .filter_map(|probe| probe.profile.as_ref().ok())
        .map(|profile| profile.profile_path.as_str())
        .collect::<std::collections::BTreeSet<_>>();

    Ok(PermissionPlatformProbe {
        platform: "windows",
        os_access: platform::windows::probe_microphone_os_access(),
        shared_profile: successful_paths.len() <= 1
            && webviews.iter().all(|probe| probe.profile.is_ok()),
        webviews,
    })
}

#[cfg(all(debug_assertions, target_os = "windows"))]
pub(crate) fn spawn_platform_probe(app: tauri::AppHandle) {
    tauri::async_runtime::spawn(async move {
        match probe_permission_platform(app.clone()).await {
            Ok(probe) => match serde_json::to_string(&probe) {
                Ok(probe) => log::info!("permission platform probe: {probe}"),
                Err(error) => log::warn!("permission platform probe serialization failed: {error}"),
            },
            Err(error) => log::warn!("permission platform probe failed: {error}"),
        }

        if let Ok(origin) = std::env::var("AGENT_FRIEND_PERMISSION_SPIKE_ROUNDTRIP_ORIGIN") {
            let window = app
                .get_webview_window("settings")
                .or_else(|| app.webview_windows().into_values().next());
            match window {
                Some(window) => match platform::windows::probe_microphone_permission_round_trip(
                    &window, &origin,
                )
                .await
                {
                    Ok(probe) => match serde_json::to_string(&probe) {
                        Ok(probe) => log::info!("permission profile round trip: {probe}"),
                        Err(error) => log::warn!(
                            "permission profile round trip serialization failed: {error}"
                        ),
                    },
                    Err(error) => log::warn!("permission profile round trip failed: {error}"),
                },
                None => log::warn!("permission profile round trip: no WebView window available"),
            }
        }
    });
}

#[cfg(all(debug_assertions, target_os = "macos"))]
#[tauri::command]
pub(crate) fn probe_permission_platform() -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({ "platform": "macos", "status": "unsupported" }))
}

#[cfg(all(debug_assertions, target_os = "macos"))]
pub(crate) fn spawn_platform_probe(app: tauri::AppHandle) {
    tauri::async_runtime::spawn(async move {
        match probe_permission_platform() {
            Ok(probe) => log::info!("permission platform probe: {probe}"),
            Err(error) => log::warn!("permission platform probe failed: {error}"),
        }
        drop(app);
    });
}

#[cfg(all(debug_assertions, not(any(target_os = "windows", target_os = "macos"))))]
#[tauri::command]
pub(crate) fn probe_permission_platform() -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({ "platform": std::env::consts::OS }))
}

#[cfg(all(debug_assertions, not(any(target_os = "windows", target_os = "macos"))))]
pub(crate) fn spawn_platform_probe(_app: tauri::AppHandle) {
    match probe_permission_platform() {
        Ok(probe) => log::info!("permission platform probe: {probe}"),
        Err(error) => log::warn!("permission platform probe failed: {error}"),
    }
}
