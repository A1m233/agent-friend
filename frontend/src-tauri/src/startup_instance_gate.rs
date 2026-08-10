use crate::paths;
use windows::{
    core::PCWSTR,
    Win32::{
        Foundation::{CloseHandle, HANDLE, WAIT_ABANDONED, WAIT_OBJECT_0},
        System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject, INFINITE},
    },
};

/// Serializes the code that runs before Tauri's single-instance plugin creates its own mutex.
pub(crate) struct StartupInstanceGate {
    handle: Option<HANDLE>,
    owned: bool,
}

// Tauri requires setup closures to be Send. The gate is acquired and released by the desktop
// run thread; the kernel handle may safely travel with that closure in between.
unsafe impl Send for StartupInstanceGate {}

impl StartupInstanceGate {
    pub(crate) fn acquire() -> Result<Self, String> {
        Self::acquire_named(&format!("Local\\{}-startup-gate-v1", paths::BUNDLE_ID))
    }

    fn acquire_named(name: &str) -> Result<Self, String> {
        let wide_name = name
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let handle = unsafe { CreateMutexW(None, false, PCWSTR(wide_name.as_ptr())) }
            .map_err(|error| format!("create startup mutex: {error}"))?;
        let wait = unsafe { WaitForSingleObject(handle, INFINITE) };
        if wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED {
            unsafe {
                let _ = CloseHandle(handle);
            }
            return Err(format!("wait for startup mutex returned {wait:?}"));
        }

        Ok(Self {
            handle: Some(handle),
            owned: true,
        })
    }

    pub(crate) fn release(mut self) -> Result<(), String> {
        self.release_inner()
    }

    fn release_inner(&mut self) -> Result<(), String> {
        let Some(handle) = self.handle.take() else {
            return Ok(());
        };

        let release_result = if self.owned {
            self.owned = false;
            unsafe { ReleaseMutex(handle) }
                .map_err(|error| format!("release startup mutex: {error}"))
        } else {
            Ok(())
        };
        let close_result =
            unsafe { CloseHandle(handle) }.map_err(|error| format!("close startup mutex: {error}"));

        release_result.and(close_result)
    }
}

impl Drop for StartupInstanceGate {
    fn drop(&mut self) {
        let _ = self.release_inner();
    }
}

#[cfg(test)]
mod tests {
    use super::StartupInstanceGate;
    use std::{
        sync::mpsc,
        thread,
        time::{Duration, SystemTime, UNIX_EPOCH},
    };

    #[test]
    fn concurrent_acquisition_waits_until_the_first_gate_is_released() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .as_nanos();
        let name = format!(
            "Local\\com.agentfriend.desktop-startup-gate-test-{}-{nonce}",
            std::process::id()
        );
        let first = StartupInstanceGate::acquire_named(&name).expect("acquire first gate");
        let (acquired_tx, acquired_rx) = mpsc::channel();
        let contender = thread::spawn(move || {
            let second = StartupInstanceGate::acquire_named(&name).expect("acquire second gate");
            acquired_tx.send(()).expect("signal second acquisition");
            second.release().expect("release second gate");
        });

        assert!(acquired_rx
            .recv_timeout(Duration::from_millis(150))
            .is_err());
        first.release().expect("release first gate");
        acquired_rx
            .recv_timeout(Duration::from_secs(2))
            .expect("second gate should acquire after release");
        contender.join().expect("join contender");
    }
}
