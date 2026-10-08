//! Low-volume, crash-resilient desktop process breadcrumbs.
//!
//! This journal is deliberately independent from `tauri-plugin-log`: it is available before
//! plugin setup and from the panic hook, and it remains bounded even when the process repeatedly
//! fails during bootstrap. Files use the existing `tauri.log.*` diagnostic allowlist.

use chrono::Utc;
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    backtrace::Backtrace,
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    sync::{atomic::AtomicU64, Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub(crate) mod history;
pub(crate) mod startup;

const ACTIVE_FILE_NAME: &str = "tauri.log.runtime";
const MAX_FILE_BYTES: u64 = 1024 * 1024;
const MAX_FILES: usize = 3;
const MAX_AGE: Duration = Duration::from_secs(30 * 24 * 60 * 60);
const MAX_PANIC_MESSAGE_CHARS: usize = 2_048;
const MAX_BACKTRACE_CHARS: usize = 32_768;

#[derive(Clone, Copy)]
struct JournalLimits {
    max_file_bytes: u64,
    max_files: usize,
    max_age: Duration,
}

impl Default for JournalLimits {
    fn default() -> Self {
        Self {
            max_file_bytes: MAX_FILE_BYTES,
            max_files: MAX_FILES,
            max_age: MAX_AGE,
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeRecord<'a> {
    schema_version: u32,
    recorded_at: String,
    process_session_id: &'a str,
    process_id: u32,
    source: &'a str,
    kind: &'a str,
    details: Value,
}

pub(crate) struct RuntimeObserver {
    directory: PathBuf,
    process_session_id: String,
    limits: JournalLimits,
    writer_lock: Mutex<()>,
    started: Instant,
    next_phase_id: AtomicU64,
    frontend_milestones: Mutex<std::collections::HashSet<String>>,
}

impl RuntimeObserver {
    pub(crate) fn start(log_directory: PathBuf) -> Arc<Self> {
        let observer = Arc::new(Self::new(log_directory, JournalLimits::default()));
        observer.prune_expired();
        observer.record(
            "lifecycle",
            "process-started",
            json!({"buildMode": if cfg!(debug_assertions) { "debug" } else { "release" }}),
        );
        observer
    }

    fn new(directory: PathBuf, limits: JournalLimits) -> Self {
        let started_at = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        Self {
            directory,
            process_session_id: format!("{}-{started_at}", std::process::id()),
            limits,
            writer_lock: Mutex::new(()),
            started: Instant::now(),
            next_phase_id: AtomicU64::new(1),
            frontend_milestones: Mutex::new(std::collections::HashSet::new()),
        }
    }

    pub(crate) fn process_session_id(&self) -> &str {
        &self.process_session_id
    }

    pub(crate) fn record(&self, source: &str, kind: &str, details: Value) {
        let record = RuntimeRecord {
            schema_version: 1,
            recorded_at: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            process_session_id: &self.process_session_id,
            process_id: std::process::id(),
            source,
            kind,
            details,
        };
        let Ok(mut bytes) = serde_json::to_vec(&record) else {
            return;
        };
        bytes.push(b'\n');

        let _guard = self
            .writer_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if let Err(error) = self.append_bytes(&bytes) {
            eprintln!("runtime observability write failed: {error}");
        }
    }

    #[allow(deprecated)]
    fn record_panic(&self, info: &std::panic::PanicInfo<'_>) {
        let message = info
            .payload()
            .downcast_ref::<&str>()
            .map(|value| (*value).to_string())
            .or_else(|| info.payload().downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "non-string panic payload".to_string());
        let location = info
            .location()
            .map(|value| format!("{}:{}:{}", value.file(), value.line(), value.column()));
        self.record(
            "panic-hook",
            "rust-panic",
            json!({
                "message": truncate_chars(&message, MAX_PANIC_MESSAGE_CHARS),
                "location": location,
                "backtrace": truncate_chars(
                    &Backtrace::force_capture().to_string(),
                    MAX_BACKTRACE_CHARS,
                ),
            }),
        );
    }

    fn append_bytes(&self, bytes: &[u8]) -> Result<(), String> {
        fs::create_dir_all(&self.directory)
            .map_err(|error| format!("create log directory failed: {error}"))?;
        let active = self.active_path();
        let current_bytes = active
            .metadata()
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        if current_bytes > 0
            && current_bytes.saturating_add(bytes.len() as u64) > self.limits.max_file_bytes
        {
            self.rotate()?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(active)
            .map_err(|error| format!("open runtime journal failed: {error}"))?;
        file.write_all(bytes)
            .and_then(|_| file.flush())
            .map_err(|error| format!("write runtime journal failed: {error}"))
    }

    fn rotate(&self) -> Result<(), String> {
        if self.limits.max_files <= 1 {
            let active = self.active_path();
            if active.exists() {
                fs::remove_file(active)
                    .map_err(|error| format!("replace runtime journal failed: {error}"))?;
            }
            return Ok(());
        }

        for index in (1..self.limits.max_files).rev() {
            let destination = self.segment_path(index);
            if index == self.limits.max_files - 1 && destination.exists() {
                fs::remove_file(&destination)
                    .map_err(|error| format!("remove oldest runtime journal failed: {error}"))?;
            }
            let source = if index == 1 {
                self.active_path()
            } else {
                self.segment_path(index - 1)
            };
            if source.exists() {
                fs::rename(&source, &destination)
                    .map_err(|error| format!("rotate runtime journal failed: {error}"))?;
            }
        }
        Ok(())
    }

    fn prune_expired(&self) {
        for path in self.paths_oldest_first() {
            let Ok(modified) = path.metadata().and_then(|metadata| metadata.modified()) else {
                continue;
            };
            let Ok(age) = modified.elapsed() else {
                continue;
            };
            if age > self.limits.max_age {
                let _ = fs::remove_file(path);
            }
        }
    }

    fn paths_oldest_first(&self) -> Vec<PathBuf> {
        let mut paths = (1..self.limits.max_files)
            .rev()
            .map(|index| self.segment_path(index))
            .collect::<Vec<_>>();
        paths.push(self.active_path());
        paths
    }

    fn active_path(&self) -> PathBuf {
        self.directory.join(ACTIVE_FILE_NAME)
    }

    fn segment_path(&self, index: usize) -> PathBuf {
        self.directory.join(format!("{ACTIVE_FILE_NAME}.{index}"))
    }
}

pub(crate) fn install_panic_hook(observer: Arc<RuntimeObserver>) {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        // Capture before allocating a backtrace or acquiring the ordinary journal lock.
        crate::native_crash::panic_observed();
        observer.record_panic(info);
        previous(info);
    }));
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    let mut chars = value.chars();
    let truncated = chars.by_ref().take(max_chars).collect::<String>();
    if chars.next().is_some() {
        format!("{truncated}…")
    } else {
        truncated
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    pub(super) fn test_directory(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "agent-friend-runtime-observability-{name}-{}-{nonce}",
            std::process::id()
        ))
    }

    #[test]
    fn journal_rotation_is_bounded() {
        let directory = test_directory("rotation");
        let observer = RuntimeObserver::new(
            directory.clone(),
            JournalLimits {
                max_file_bytes: 180,
                max_files: 3,
                max_age: Duration::from_secs(60),
            },
        );

        for index in 0..12 {
            observer.record("test", "event", json!({"index": index}));
        }

        let files = observer
            .paths_oldest_first()
            .into_iter()
            .filter(|path| path.is_file())
            .collect::<Vec<_>>();
        assert_eq!(files.len(), 3);
        assert!(files
            .iter()
            .all(|path| path.metadata().expect("metadata").len() <= 360));
        let latest = fs::read_to_string(observer.active_path()).expect("latest journal");
        assert!(latest.contains("\"index\":11"));

        fs::remove_dir_all(directory).expect("cleanup");
    }

    #[test]
    fn records_are_correlated_by_process_session() {
        let directory = test_directory("session");
        let observer = RuntimeObserver::new(
            directory.clone(),
            JournalLimits {
                max_file_bytes: 4096,
                max_files: 2,
                max_age: Duration::from_secs(60),
            },
        );

        observer.record("lifecycle", "process-started", json!({}));
        observer.record("lifecycle", "process-exit-completed", json!({}));

        let content = fs::read_to_string(observer.active_path()).expect("journal");
        let lines = content.lines().collect::<Vec<_>>();
        assert_eq!(lines.len(), 2);
        assert!(lines
            .iter()
            .all(|line| line.contains(observer.process_session_id())));
        assert!(lines[0].contains("process-started"));
        assert!(lines[1].contains("process-exit-completed"));

        fs::remove_dir_all(directory).expect("cleanup");
    }

    #[test]
    fn truncation_preserves_utf8_boundaries() {
        assert_eq!(truncate_chars("桌宠异常", 2), "桌宠…");
        assert_eq!(truncate_chars("ok", 10), "ok");
    }
}
