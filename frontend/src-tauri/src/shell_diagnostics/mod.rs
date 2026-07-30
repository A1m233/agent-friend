mod journal;
mod manager;
mod model;
mod platform;

use crate::paths;
use std::sync::Arc;

pub(crate) use manager::ShellDiagnosticsManager;

pub(crate) fn create_manager() -> Arc<ShellDiagnosticsManager> {
    Arc::new(ShellDiagnosticsManager::new(paths::shell_diagnostics_dir()))
}
