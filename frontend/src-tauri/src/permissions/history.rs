use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

use serde::{Deserialize, Serialize};

use super::model::{PermissionId, SystemPermissionState};

const HISTORY_SCHEMA_VERSION: u32 = 1;
static TEMP_FILE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionHistoryEntry {
    pub handled_revision: u32,
    pub deferred_revision: Option<u32>,
    pub ever_granted: bool,
    pub last_observed_system_state: Option<SystemPermissionState>,
    pub last_observed_at_ms: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionHistory {
    schema_version: u32,
    pub permissions: BTreeMap<PermissionId, PermissionHistoryEntry>,
}

impl Default for PermissionHistory {
    fn default() -> Self {
        Self {
            schema_version: HISTORY_SCHEMA_VERSION,
            permissions: BTreeMap::new(),
        }
    }
}

impl PermissionHistory {
    pub(crate) fn entry(&self, id: PermissionId) -> PermissionHistoryEntry {
        self.permissions.get(&id).cloned().unwrap_or_default()
    }

    pub(crate) fn entry_mut(&mut self, id: PermissionId) -> &mut PermissionHistoryEntry {
        self.permissions.entry(id).or_default()
    }

    pub(crate) fn load(path: &Path) -> Result<Self, HistoryLoadError> {
        let raw = fs::read_to_string(path).map_err(HistoryLoadError::Io)?;
        let history: Self = serde_json::from_str(&raw).map_err(HistoryLoadError::Parse)?;
        if history.schema_version != HISTORY_SCHEMA_VERSION {
            return Err(HistoryLoadError::UnsupportedSchema(history.schema_version));
        }
        Ok(history)
    }

    pub(crate) fn load_or_default(path: &Path) -> Self {
        match Self::load(path) {
            Ok(history) => history,
            Err(HistoryLoadError::Io(error)) if error.kind() == io::ErrorKind::NotFound => {
                Self::default()
            }
            Err(error) => {
                log::warn!("permission history load failed; continuing empty: {error}");
                Self::default()
            }
        }
    }

    pub(crate) fn save(&self, path: &Path) -> Result<(), HistorySaveError> {
        let parent = path
            .parent()
            .ok_or_else(|| HistorySaveError::InvalidPath(path.to_path_buf()))?;
        fs::create_dir_all(parent).map_err(HistorySaveError::Io)?;

        let temp_path = temporary_path(path);
        let result = (|| {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp_path)
                .map_err(HistorySaveError::Io)?;
            serde_json::to_writer_pretty(&mut file, self).map_err(HistorySaveError::Serialize)?;
            file.write_all(b"\n").map_err(HistorySaveError::Io)?;
            file.sync_all().map_err(HistorySaveError::Io)?;
            replace_file(&temp_path, path).map_err(HistorySaveError::Io)
        })();

        if result.is_err() {
            let _ = fs::remove_file(&temp_path);
        }
        result
    }
}

#[derive(Debug)]
pub(crate) enum HistoryLoadError {
    Io(io::Error),
    Parse(serde_json::Error),
    UnsupportedSchema(u32),
}

impl std::fmt::Display for HistoryLoadError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "I/O error: {error}"),
            Self::Parse(error) => write!(formatter, "invalid JSON: {error}"),
            Self::UnsupportedSchema(version) => {
                write!(formatter, "unsupported schema version {version}")
            }
        }
    }
}

#[derive(Debug)]
pub(crate) enum HistorySaveError {
    InvalidPath(PathBuf),
    Io(io::Error),
    Serialize(serde_json::Error),
}

impl std::fmt::Display for HistorySaveError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidPath(path) => write!(formatter, "path has no parent: {}", path.display()),
            Self::Io(error) => write!(formatter, "I/O error: {error}"),
            Self::Serialize(error) => write!(formatter, "serialization error: {error}"),
        }
    }
}

fn temporary_path(path: &Path) -> PathBuf {
    let sequence = TEMP_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("permission-state.json");
    path.with_file_name(format!(
        ".{file_name}.{}.{}.tmp",
        std::process::id(),
        sequence
    ))
}

#[cfg(target_os = "windows")]
fn replace_file(source: &Path, destination: &Path) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows::{
        core::PCWSTR,
        Win32::Storage::FileSystem::{
            MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        },
    };

    let source = source
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    unsafe {
        MoveFileExW(
            PCWSTR(source.as_ptr()),
            PCWSTR(destination.as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
        .map_err(io::Error::other)
    }
}

#[cfg(not(target_os = "windows"))]
fn replace_file(source: &Path, destination: &Path) -> io::Result<()> {
    fs::rename(source, destination)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "agent-friend-permission-history-{}-{name}",
            std::process::id()
        ))
    }

    #[test]
    fn history_round_trips_and_replaces_an_existing_file() {
        let directory = test_path("round-trip");
        let path = directory.join("permission-state.json");
        let _ = fs::remove_dir_all(&directory);

        let mut history = PermissionHistory::default();
        history.entry_mut(PermissionId::Microphone).ever_granted = true;
        history.save(&path).unwrap();
        history.entry_mut(PermissionId::Microphone).handled_revision = 1;
        history.save(&path).unwrap();

        assert_eq!(PermissionHistory::load(&path).unwrap(), history);
        assert_eq!(
            fs::read_dir(&directory).unwrap().count(),
            1,
            "atomic temp file should be removed after replacement"
        );
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn corrupt_or_future_history_fails_closed_to_empty() {
        let directory = test_path("fail-closed");
        let path = directory.join("permission-state.json");
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).unwrap();

        fs::write(&path, "not-json").unwrap();
        assert_eq!(
            PermissionHistory::load_or_default(&path),
            PermissionHistory::default()
        );

        fs::write(&path, r#"{"schemaVersion":99,"permissions":{}}"#).unwrap();
        assert_eq!(
            PermissionHistory::load_or_default(&path),
            PermissionHistory::default()
        );
        fs::remove_dir_all(directory).unwrap();
    }
}
