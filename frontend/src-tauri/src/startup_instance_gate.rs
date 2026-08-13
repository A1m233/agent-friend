pub(crate) struct StartupInstanceGate {
    inner: Option<platform::Gate>,
}

impl StartupInstanceGate {
    pub(crate) fn acquire(instance_domain: &str) -> Result<Self, String> {
        validate_instance_domain(instance_domain)?;
        platform::acquire(instance_domain).map(|inner| Self { inner: Some(inner) })
    }

    pub(crate) fn release(mut self) -> Result<(), String> {
        self.release_inner()
    }

    fn release_inner(&mut self) -> Result<(), String> {
        let Some(mut inner) = self.inner.take() else {
            return Ok(());
        };
        platform::release(&mut inner)
    }
}

impl Drop for StartupInstanceGate {
    fn drop(&mut self) {
        let _ = self.release_inner();
    }
}

fn validate_instance_domain(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 80
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err(format!(
            "identity_contract_invalid: invalid instance domain {value:?}"
        ));
    }
    Ok(())
}

#[cfg(target_os = "windows")]
mod platform {
    use windows::{
        core::PCWSTR,
        Win32::{
            Foundation::{CloseHandle, HANDLE, WAIT_ABANDONED, WAIT_OBJECT_0},
            System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject, INFINITE},
        },
    };

    pub(super) struct Gate {
        handle: Option<HANDLE>,
        owned: bool,
    }

    // Tauri setup closures are Send. Acquisition and release still happen on the desktop run
    // thread; moving the kernel handle into the closure is safe.
    unsafe impl Send for Gate {}

    pub(super) fn acquire(instance_domain: &str) -> Result<Gate, String> {
        acquire_named(&format!(
            "Local\\com.agentfriend.desktop-{instance_domain}-startup-gate-v2"
        ))
    }

    fn acquire_named(name: &str) -> Result<Gate, String> {
        let wide_name = name
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let handle = unsafe { CreateMutexW(None, false, PCWSTR(wide_name.as_ptr())) }
            .map_err(|error| format!("startup_gate_unavailable: create mutex: {error}"))?;
        let wait = unsafe { WaitForSingleObject(handle, INFINITE) };
        if wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED {
            unsafe {
                let _ = CloseHandle(handle);
            }
            return Err(format!(
                "startup_gate_unavailable: wait for mutex returned {wait:?}"
            ));
        }

        Ok(Gate {
            handle: Some(handle),
            owned: true,
        })
    }

    pub(super) fn release(gate: &mut Gate) -> Result<(), String> {
        let Some(handle) = gate.handle.take() else {
            return Ok(());
        };
        let release_result = if gate.owned {
            gate.owned = false;
            unsafe { ReleaseMutex(handle) }
                .map_err(|error| format!("startup_gate_unavailable: release mutex: {error}"))
        } else {
            Ok(())
        };
        let close_result = unsafe { CloseHandle(handle) }
            .map_err(|error| format!("startup_gate_unavailable: close mutex: {error}"));
        release_result.and(close_result)
    }

    #[cfg(test)]
    pub(super) fn acquire_test(name: &str) -> Result<Gate, String> {
        acquire_named(name)
    }
}

#[cfg(not(target_os = "windows"))]
mod platform {
    use crate::paths;
    use fs4::FileExt;
    use std::{
        fs::{File, OpenOptions},
        path::PathBuf,
    };

    pub(super) struct Gate {
        file: Option<File>,
        #[allow(dead_code)]
        path: PathBuf,
    }

    pub(super) fn acquire(instance_domain: &str) -> Result<Gate, String> {
        let directory = paths::instance_coordination_dir();
        std::fs::create_dir_all(&directory).map_err(|error| {
            format!("startup_gate_unavailable: create coordination directory: {error}")
        })?;
        acquire_path(directory.join(format!("{instance_domain}.startup.lock")))
    }

    fn acquire_path(path: PathBuf) -> Result<Gate, String> {
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&path)
            .map_err(|error| format!("startup_gate_unavailable: open file lock: {error}"))?;
        file.lock()
            .map_err(|error| format!("startup_gate_unavailable: lock file: {error}"))?;
        Ok(Gate {
            file: Some(file),
            path,
        })
    }

    pub(super) fn release(gate: &mut Gate) -> Result<(), String> {
        let Some(file) = gate.file.take() else {
            return Ok(());
        };
        file.unlock()
            .map_err(|error| format!("startup_gate_unavailable: unlock file: {error}"))
    }

    #[cfg(test)]
    pub(super) fn acquire_test(path: PathBuf) -> Result<Gate, String> {
        acquire_path(path)
    }
}

#[cfg(test)]
mod tests {
    use super::{validate_instance_domain, StartupInstanceGate};
    use std::{
        sync::mpsc,
        thread,
        time::{Duration, SystemTime, UNIX_EPOCH},
    };

    #[test]
    fn instance_domain_is_safe_for_kernel_and_file_names() {
        assert!(validate_instance_domain("agent-friend-production").is_ok());
        assert!(validate_instance_domain("").is_err());
        assert!(validate_instance_domain("Agent Friend").is_err());
        assert!(validate_instance_domain("../agent-friend").is_err());
    }

    #[test]
    fn concurrent_acquisition_waits_until_the_first_gate_is_released() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .as_nanos();

        #[cfg(target_os = "windows")]
        let (first, acquire_second) = {
            let name = format!(
                "Local\\com.agentfriend.desktop-startup-gate-test-{}-{nonce}",
                std::process::id()
            );
            let first = super::platform::acquire_test(&name).expect("acquire first gate");
            let acquire_second = move || super::platform::acquire_test(&name);
            (StartupInstanceGate { inner: Some(first) }, acquire_second)
        };

        #[cfg(not(target_os = "windows"))]
        let (first, acquire_second) = {
            let path = std::env::temp_dir().join(format!(
                "agent-friend-startup-gate-test-{}-{nonce}.lock",
                std::process::id()
            ));
            let first = super::platform::acquire_test(path.clone()).expect("acquire first gate");
            let acquire_second = move || super::platform::acquire_test(path);
            (StartupInstanceGate { inner: Some(first) }, acquire_second)
        };

        let (acquired_tx, acquired_rx) = mpsc::channel();
        let contender = thread::spawn(move || {
            let second = acquire_second().expect("acquire second gate");
            acquired_tx.send(()).expect("signal second acquisition");
            let mut second = StartupInstanceGate {
                inner: Some(second),
            };
            second.release_inner().expect("release second gate");
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
