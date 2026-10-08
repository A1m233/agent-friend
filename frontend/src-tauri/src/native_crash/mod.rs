//! Local native-failure evidence. Capture is deliberately independent of Tauri and its logger.
mod store;
#[cfg(windows)]
mod windows;

pub(crate) use store::{ExportDump, NativeCrashExport};

pub(crate) fn export_for_bundle(include_raw: bool) -> NativeCrashExport {
    let mut result = store::export(&crate::paths::native_crash_dir(), include_raw);
    #[cfg(windows)]
    {
        result.failures.extend(windows::current_failures());
        if let Some((session, incident, code)) = windows::current_handoff_failure() {
            // Live health wins over a stale on-disk ready record while the observer is stalled.
            result.coverage["captureStatus"] = serde_json::json!("unavailable");
            if let Some(sessions) = result.coverage["sessions"].as_array_mut() {
                for value in sessions
                    .iter_mut()
                    .filter(|v| v["sessionId"].as_str() == Some(&session))
                {
                    value["captureStatus"] = serde_json::json!("degraded");
                    if let Some(failures) = value["failures"].as_array_mut() {
                        if !failures.iter().any(|v| v.as_str() == Some(code)) {
                            failures.push(serde_json::json!(code));
                        }
                    }
                }
            }
            let dump_name = format!("diagnostics/native-crash/dumps/{incident}.dmp");
            result.dumps.retain(|dump| dump.archive_name != dump_name);
            for (_, value) in result
                .incidents
                .iter_mut()
                .filter(|(_, v)| v["incidentId"].as_str() == Some(&incident))
            {
                value["dump"] = serde_json::json!({"status":"failed","errorCode":"capture-context-expired","sizeBytes":null,"sha256":null,"archiveEntry":null});
            }
            result.coverage["rawDumpCount"] = serde_json::json!(result.dumps.len());
        }
        result.failures.sort();
        result.failures.dedup();
        result.coverage["failures"] = serde_json::json!(result.failures);
    }
    result
}

#[cfg(test)]
pub(crate) fn export_from_test_dir(root: &std::path::Path, include_raw: bool) -> NativeCrashExport {
    store::export(root, include_raw)
}

/// Only inherited, authenticated capabilities can enter the private helper modes.
pub fn run_internal_mode() -> Option<i32> {
    #[cfg(windows)]
    {
        windows::run_internal_mode()
    }
    #[cfg(not(windows))]
    {
        None
    }
}

pub(crate) fn start(session: &str) {
    #[cfg(windows)]
    windows::start(session);
    #[cfg(not(windows))]
    let _ = session;
}

pub fn desktop_returned() {
    orderly_exit("normal-return");
}

pub(crate) fn orderly_exit(reason: &str) {
    #[cfg(windows)]
    windows::orderly_exit(reason);
    #[cfg(not(windows))]
    let _ = reason;
}

pub(crate) fn panic_observed() {
    #[cfg(windows)]
    windows::panic_observed();
}

#[cfg(windows)]
pub(crate) fn register_service(child: &std::process::Child, role: &str, generation: u64) {
    windows::register_service(child, role, generation);
}
