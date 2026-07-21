use super::{PowerInhibitor, PowerPlatformAdapter};
use crate::power_policy::model::{PlatformCapability, PowerPolicyError, PowerSource};
use core_foundation_sys::{
    base::{kCFAllocatorDefault, CFEqual, CFRelease, CFTypeRef},
    string::{kCFStringEncodingUTF8, CFStringCreateWithCString, CFStringRef},
};
use std::ffi::CString;
use tauri_plugin_opener::OpenerExt;

type IOPMAssertionId = u32;
const IOPM_ASSERTION_LEVEL_ON: u32 = 255;
const IO_SUCCESS: i32 = 0;

#[link(name = "IOKit", kind = "framework")]
extern "C" {
    static kIOPMAssertionTypePreventUserIdleSystemSleep: CFStringRef;
    static kIOPSACPowerValue: CFStringRef;
    static kIOPSBatteryPowerValue: CFStringRef;

    fn IOPMAssertionCreateWithName(
        assertion_type: CFStringRef,
        assertion_level: u32,
        assertion_name: CFStringRef,
        assertion_id: *mut IOPMAssertionId,
    ) -> i32;
    fn IOPMAssertionRelease(assertion_id: IOPMAssertionId) -> i32;
    fn IOPSCopyPowerSourcesInfo() -> CFTypeRef;
    fn IOPSGetProvidingPowerSourceType(blob: CFTypeRef) -> CFStringRef;
}

pub(crate) struct MacosPowerPlatformAdapter {
    app: tauri::AppHandle,
}

impl MacosPowerPlatformAdapter {
    pub(crate) fn new(app: tauri::AppHandle) -> Self {
        Self { app }
    }
}

impl PowerPlatformAdapter for MacosPowerPlatformAdapter {
    fn capability(&self) -> PlatformCapability {
        PlatformCapability::Supported
    }

    fn power_source(&self) -> Result<PowerSource, PowerPolicyError> {
        unsafe {
            let blob = IOPSCopyPowerSourcesInfo();
            if blob.is_null() {
                return Err(PowerPolicyError::new(
                    "power_source_unavailable",
                    "IOPSCopyPowerSourcesInfo returned null",
                ));
            }
            let source = IOPSGetProvidingPowerSourceType(blob);
            let result = if source.is_null() {
                PowerSource::Unknown
            } else if CFEqual(source as CFTypeRef, kIOPSACPowerValue as CFTypeRef) != 0 {
                PowerSource::Ac
            } else if CFEqual(source as CFTypeRef, kIOPSBatteryPowerValue as CFTypeRef) != 0 {
                PowerSource::Battery
            } else {
                PowerSource::Unknown
            };
            CFRelease(blob);
            Ok(result)
        }
    }

    fn acquire(&self, reason: &str) -> Result<Box<dyn PowerInhibitor>, PowerPolicyError> {
        let reason = CString::new(reason).map_err(|error| {
            PowerPolicyError::new("power_inhibit_acquire_failed", error.to_string())
        })?;
        unsafe {
            let reason_ref = CFStringCreateWithCString(
                kCFAllocatorDefault,
                reason.as_ptr(),
                kCFStringEncodingUTF8,
            );
            if reason_ref.is_null() {
                return Err(PowerPolicyError::new(
                    "power_inhibit_acquire_failed",
                    "failed to create assertion reason",
                ));
            }
            let mut id = 0;
            let status = IOPMAssertionCreateWithName(
                kIOPMAssertionTypePreventUserIdleSystemSleep,
                IOPM_ASSERTION_LEVEL_ON,
                reason_ref,
                &mut id,
            );
            CFRelease(reason_ref as CFTypeRef);
            if status != IO_SUCCESS {
                return Err(PowerPolicyError::new(
                    "power_inhibit_acquire_failed",
                    format!("IOPMAssertionCreateWithName failed: {status}"),
                ));
            }
            Ok(Box::new(MacosPowerInhibitor {
                id,
                released: false,
            }))
        }
    }

    fn open_power_settings(&self) -> Result<(), PowerPolicyError> {
        self.app
            .opener()
            .open_url(
                "x-apple.systempreferences:com.apple.preference.battery",
                None::<&str>,
            )
            .or_else(|_| {
                self.app
                    .opener()
                    .open_url("x-apple.systempreferences:", None::<&str>)
            })
            .map_err(|error| PowerPolicyError::new("power_settings_open_failed", error.to_string()))
    }
}

struct MacosPowerInhibitor {
    id: IOPMAssertionId,
    released: bool,
}

impl PowerInhibitor for MacosPowerInhibitor {
    fn release(&mut self) -> Result<(), PowerPolicyError> {
        if self.released {
            return Ok(());
        }
        let status = unsafe { IOPMAssertionRelease(self.id) };
        if status != IO_SUCCESS {
            return Err(PowerPolicyError::new(
                "power_inhibit_release_failed",
                format!("IOPMAssertionRelease failed: {status}"),
            ));
        }
        self.released = true;
        Ok(())
    }
}

impl Drop for MacosPowerInhibitor {
    fn drop(&mut self) {
        if !self.released {
            let _ = unsafe { IOPMAssertionRelease(self.id) };
            self.released = true;
        }
    }
}
