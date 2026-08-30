mod model;
mod supervisor;

pub use model::{RuntimeServiceKind, RuntimeServicePhase, RuntimeServiceSnapshot};
pub use supervisor::RuntimeServiceSupervisor;

use crate::settings::Settings;
use std::sync::{Arc, Mutex as StdMutex};
use tauri::{AppHandle, State};

#[tauri::command]
pub async fn get_runtime_service_snapshot(
    service: RuntimeServiceKind,
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeServiceSnapshot, String> {
    Ok(state.snapshot(service).await)
}

#[tauri::command]
pub async fn start_runtime_service(
    app: AppHandle,
    service: RuntimeServiceKind,
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeServiceSnapshot, String> {
    state.start(Some(&app), service).await
}

#[tauri::command]
pub async fn stop_runtime_service(
    app: AppHandle,
    service: RuntimeServiceKind,
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeServiceSnapshot, String> {
    state.stop(Some(&app), service).await
}

#[tauri::command]
pub async fn restart_runtime_service(
    app: AppHandle,
    service: RuntimeServiceKind,
    reason: Option<String>,
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeServiceSnapshot, String> {
    match reason {
        Some(reason) => {
            state
                .restart_with_reason(Some(&app), service, &reason)
                .await
        }
        None => state.restart(Some(&app), service).await,
    }
}

#[tauri::command]
pub async fn prepare_voice_runtime(
    app: AppHandle,
    settings: State<'_, Arc<StdMutex<Settings>>>,
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeServiceSnapshot, String> {
    let consent_accepted = settings
        .lock()
        .map_err(|_| "settings lock poisoned".to_string())?
        .voice_tunnel_consent_accepted;
    if !consent_accepted {
        return Err("voice_tunnel_consent_required".to_string());
    }
    state.prepare_voice_runtime(Some(&app)).await
}

#[tauri::command]
pub async fn stop_voice_tunnel(
    app: AppHandle,
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeServiceSnapshot, String> {
    state.stop_voice_tunnel(Some(&app)).await
}
