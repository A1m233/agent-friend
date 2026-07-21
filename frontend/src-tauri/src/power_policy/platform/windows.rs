use super::{PowerInhibitor, PowerPlatformAdapter};
use crate::power_policy::model::{PlatformCapability, PowerPolicyError, PowerSource};
use std::ffi::c_void;
use tauri_plugin_opener::OpenerExt;
use windows::{
    core::PWSTR,
    Win32::{
        Foundation::{CloseHandle, HANDLE},
        System::{
            Power::{
                GetSystemPowerStatus, PowerClearRequest, PowerCreateRequest,
                PowerRequestSystemRequired, PowerSetRequest, SYSTEM_POWER_STATUS,
            },
            Threading::{POWER_REQUEST_CONTEXT_SIMPLE_STRING, REASON_CONTEXT, REASON_CONTEXT_0},
        },
    },
};

pub(crate) struct WindowsPowerPlatformAdapter {
    app: tauri::AppHandle,
}

impl WindowsPowerPlatformAdapter {
    pub(crate) fn new(app: tauri::AppHandle) -> Self {
        Self { app }
    }
}

impl PowerPlatformAdapter for WindowsPowerPlatformAdapter {
    fn capability(&self) -> PlatformCapability {
        PlatformCapability::Supported
    }

    fn power_source(&self) -> Result<PowerSource, PowerPolicyError> {
        let mut status = SYSTEM_POWER_STATUS::default();
        unsafe { GetSystemPowerStatus(&mut status) }.map_err(|error| {
            PowerPolicyError::new("power_source_unavailable", error.to_string())
        })?;
        Ok(match status.ACLineStatus {
            0 => PowerSource::Battery,
            1 => PowerSource::Ac,
            _ => PowerSource::Unknown,
        })
    }

    fn acquire(&self, reason: &str) -> Result<Box<dyn PowerInhibitor>, PowerPolicyError> {
        acquire_windows_inhibitor(reason)
            .map(|inhibitor| Box::new(inhibitor) as Box<dyn PowerInhibitor>)
    }

    fn open_power_settings(&self) -> Result<(), PowerPolicyError> {
        self.app
            .opener()
            .open_url("ms-settings:powersleep", None::<&str>)
            .map_err(|error| PowerPolicyError::new("power_settings_open_failed", error.to_string()))
    }
}

struct WindowsPowerInhibitor {
    handle: usize,
    request_set: bool,
    closed: bool,
}

impl WindowsPowerInhibitor {
    fn raw_handle(&self) -> HANDLE {
        HANDLE(self.handle as *mut c_void)
    }
}

impl PowerInhibitor for WindowsPowerInhibitor {
    fn release(&mut self) -> Result<(), PowerPolicyError> {
        if self.closed {
            return Ok(());
        }
        let handle = self.raw_handle();
        if self.request_set {
            unsafe { PowerClearRequest(handle, PowerRequestSystemRequired) }.map_err(|error| {
                PowerPolicyError::new("power_inhibit_release_failed", error.to_string())
            })?;
            self.request_set = false;
        }
        unsafe { CloseHandle(handle) }.map_err(|error| {
            PowerPolicyError::new("power_inhibit_release_failed", error.to_string())
        })?;
        self.closed = true;
        Ok(())
    }
}

impl Drop for WindowsPowerInhibitor {
    fn drop(&mut self) {
        if !self.closed {
            let handle = self.raw_handle();
            if self.request_set {
                let _ = unsafe { PowerClearRequest(handle, PowerRequestSystemRequired) };
            }
            let _ = unsafe { CloseHandle(handle) };
            self.request_set = false;
            self.closed = true;
        }
    }
}

fn acquire_windows_inhibitor(reason: &str) -> Result<WindowsPowerInhibitor, PowerPolicyError> {
    let mut reason_utf16 = reason
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let context = REASON_CONTEXT {
        Version: 0,
        Flags: POWER_REQUEST_CONTEXT_SIMPLE_STRING,
        Reason: REASON_CONTEXT_0 {
            SimpleReasonString: PWSTR(reason_utf16.as_mut_ptr()),
        },
    };
    let handle = unsafe { PowerCreateRequest(&context) }.map_err(|error| {
        PowerPolicyError::new("power_inhibit_acquire_failed", error.to_string())
    })?;
    if let Err(error) = unsafe { PowerSetRequest(handle, PowerRequestSystemRequired) } {
        let _ = unsafe { CloseHandle(handle) };
        return Err(PowerPolicyError::new(
            "power_inhibit_acquire_failed",
            error.to_string(),
        ));
    }
    Ok(WindowsPowerInhibitor {
        handle: handle.0 as usize,
        request_set: true,
        closed: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Write, time::Duration};

    #[test]
    #[ignore = "run through scripts/power-policy-smoke/run.ps1 on a Windows device"]
    fn power_request_smoke_holds_until_timeout() {
        let seconds = std::env::var("AGENT_FRIEND_POWER_SMOKE_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(15);
        let mut inhibitor = acquire_windows_inhibitor(
            "agent-friend power policy smoke (display sleep remains allowed)",
        )
        .expect("acquire Windows power request");
        println!("AGENT_FRIEND_POWER_SMOKE_ACTIVE");
        std::io::stdout().flush().expect("flush smoke marker");
        std::thread::sleep(Duration::from_secs(seconds));
        inhibitor.release().expect("release Windows power request");
    }
}
