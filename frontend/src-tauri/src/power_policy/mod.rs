mod evaluator;
mod manager;
pub mod model;
mod platform;

pub use manager::PowerPolicyManager;
pub use model::{BackgroundOnlinePolicy, PowerPolicySnapshot};

use std::sync::{Arc, Mutex};
use tauri::{AppHandle, State};

use crate::{runtime_services::RuntimeServiceSupervisor, settings::Settings};

pub fn create_manager(
    app: AppHandle,
    settings: Arc<Mutex<Settings>>,
    supervisor: Arc<RuntimeServiceSupervisor>,
) -> Arc<PowerPolicyManager> {
    Arc::new(PowerPolicyManager::new(
        settings,
        supervisor,
        platform::create_adapter(app),
    ))
}

#[tauri::command]
pub async fn get_power_policy_snapshot(
    state: State<'_, Arc<PowerPolicyManager>>,
) -> Result<PowerPolicySnapshot, String> {
    Ok(state.snapshot().await)
}

#[tauri::command]
pub async fn refresh_power_policy(
    app: AppHandle,
    state: State<'_, Arc<PowerPolicyManager>>,
) -> Result<PowerPolicySnapshot, String> {
    Ok(state.refresh(&app).await)
}

#[tauri::command]
pub fn open_power_settings(state: State<'_, Arc<PowerPolicyManager>>) -> Result<(), String> {
    state
        .open_power_settings()
        .map_err(|error| format!("{}: {}", error.code, error.message))
}
