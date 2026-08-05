mod journal;
mod manager;
mod model;
mod platform;

use crate::paths;
use std::collections::BTreeMap;
use std::sync::Arc;
use tauri::{AppHandle, Manager};

pub(crate) use manager::ShellDiagnosticsManager;

pub(crate) fn create_manager() -> Arc<ShellDiagnosticsManager> {
    Arc::new(ShellDiagnosticsManager::new(paths::shell_diagnostics_dir()))
}

/// Capture an immediate shell snapshot for an incident artifact without waiting for journal export.
pub(crate) fn capture_current_snapshot(app: &AppHandle) -> Result<serde_json::Value, String> {
    #[cfg(target_os = "windows")]
    let app_windows = app
        .webview_windows()
        .into_iter()
        .filter_map(|(label, window)| window.hwnd().ok().map(|hwnd| (hwnd.0 as usize, label)))
        .collect::<BTreeMap<_, _>>();

    #[cfg(not(target_os = "windows"))]
    let app_windows = BTreeMap::new();

    serde_json::to_value(platform::capture_snapshot(&app_windows))
        .map_err(|error| format!("serialize immediate shell snapshot failed: {error}"))
}
