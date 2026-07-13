mod model;
mod supervisor;

pub use model::{RuntimeServiceKind, RuntimeServiceSnapshot};
pub use supervisor::RuntimeServiceSupervisor;

use std::sync::Arc;
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
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeServiceSnapshot, String> {
    state.restart(Some(&app), service).await
}
