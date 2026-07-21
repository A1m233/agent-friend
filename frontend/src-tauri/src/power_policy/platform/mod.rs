use super::model::{PlatformCapability, PowerPolicyError, PowerSource};
use std::sync::Arc;

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "windows")]
mod windows;

pub(crate) trait PowerInhibitor: Send {
    fn release(&mut self) -> Result<(), PowerPolicyError>;
}

pub(crate) trait PowerPlatformAdapter: Send + Sync {
    fn capability(&self) -> PlatformCapability;
    fn power_source(&self) -> Result<PowerSource, PowerPolicyError>;
    fn acquire(&self, reason: &str) -> Result<Box<dyn PowerInhibitor>, PowerPolicyError>;
    fn open_power_settings(&self) -> Result<(), PowerPolicyError>;
}

#[cfg(target_os = "windows")]
pub(crate) fn create_adapter(app: tauri::AppHandle) -> Arc<dyn PowerPlatformAdapter> {
    Arc::new(windows::WindowsPowerPlatformAdapter::new(app))
}

#[cfg(target_os = "macos")]
pub(crate) fn create_adapter(app: tauri::AppHandle) -> Arc<dyn PowerPlatformAdapter> {
    Arc::new(macos::MacosPowerPlatformAdapter::new(app))
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
pub(crate) fn create_adapter(_app: tauri::AppHandle) -> Arc<dyn PowerPlatformAdapter> {
    Arc::new(UnsupportedPowerPlatformAdapter)
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
struct UnsupportedPowerPlatformAdapter;

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
impl PowerPlatformAdapter for UnsupportedPowerPlatformAdapter {
    fn capability(&self) -> PlatformCapability {
        PlatformCapability::Unsupported
    }

    fn power_source(&self) -> Result<PowerSource, PowerPolicyError> {
        Ok(PowerSource::Unknown)
    }

    fn acquire(&self, _reason: &str) -> Result<Box<dyn PowerInhibitor>, PowerPolicyError> {
        Err(PowerPolicyError::new(
            "power_inhibit_unsupported",
            "power inhibition is not supported on this platform",
        ))
    }

    fn open_power_settings(&self) -> Result<(), PowerPolicyError> {
        Err(PowerPolicyError::new(
            "power_settings_unsupported",
            "power settings cannot be opened on this platform",
        ))
    }
}
