//! Single background owner, append-only segments, independent incident budgets.
use super::model::{identity, numeric_id, METADATA_BYTES, SCAN_FILES};
use chrono::Utc;
use fs4::fs_std::FileExt;
use serde_json::{json, Value};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub(super) const HISTORY_BYTES: u64 = 16 * 1024 * 1024;
pub(super) const SEGMENT_BYTES: u64 = 2 * 1024 * 1024;
pub(super) const INCIDENT_BYTES: u64 = 8 * 1024 * 1024;
pub(super) const INCIDENT_FILE_BYTES: usize = 256 * 1024;
const STATE_BYTES: usize = 128 * 1024;
const DAY_MS: i64 = 86_400_000;

#[derive(Clone, Copy)]
pub(super) struct Limits {
    pub history: u64,
    pub segment: u64,
    pub incidents: u64,
    pub incident_file: usize,
    pub incident_count: usize,
    pub history_age_ms: i64,
    pub incident_age_ms: i64,
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            history: HISTORY_BYTES,
            segment: SEGMENT_BYTES,
            incidents: INCIDENT_BYTES,
            incident_file: INCIDENT_FILE_BYTES,
            incident_count: 32,
            history_age_ms: 7 * DAY_MS,
            incident_age_ms: 30 * DAY_MS,
        }
    }
}

pub(super) struct OwnedFile {
    pub path: PathBuf,
    pub kind: &'static str,
    pub bytes: u64,
    pub created_ms: i64,
}

pub(super) fn safe_regular(path: &Path) -> std::io::Result<bool> {
    let metadata = fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Ok(false);
        }
    }
    Ok(metadata.is_file() && !metadata.file_type().is_symlink())
}

pub(super) fn safe_directory(path: &Path) -> std::io::Result<bool> {
    // Reject reparse/symlink components, not only the final dedicated directory.
    for component in path.ancestors() {
        if component.as_os_str().is_empty() {
            continue;
        }
        let metadata = fs::symlink_metadata(component)?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Ok(false);
            }
        }
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Ok(false);
        }
    }
    Ok(true)
}

pub(super) fn file_kind(name: &str) -> Option<&'static str> {
    for (prefix, suffix, kind) in [
        ("history-", ".jsonl", "history"),
        ("incident-", ".json", "incident"),
    ] {
        if let Some(id) = name
            .strip_prefix(prefix)
            .and_then(|s| s.strip_suffix(suffix))
        {
            if numeric_id(id) {
                return Some(kind);
            }
        }
    }
    None
}

pub(super) fn scan(directory: &Path) -> Result<Vec<OwnedFile>, &'static str> {
    if !safe_directory(directory).map_err(|_| "directory-unreadable")? {
        return Err("unsafe-directory");
    }
    let mut result = Vec::new();
    for (count, entry) in fs::read_dir(directory)
        .map_err(|_| "directory-unreadable")?
        .enumerate()
    {
        if count >= SCAN_FILES {
            return Err("scan-limit");
        }
        let entry = entry.map_err(|_| "directory-unreadable")?;
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        let Some(kind) = file_kind(name) else {
            continue;
        };
        if !safe_regular(&entry.path()).map_err(|_| "file-unreadable")? {
            return Err("unsafe-file");
        }
        // Windows enumeration metadata can retain an old length while a writer is open.
        let metadata = File::open(entry.path())
            .and_then(|file| file.metadata())
            .map_err(|_| "file-unreadable")?;
        // Producer filenames embed segment/incident creation UTC milliseconds before the suffix.
        let created_ms = name
            .trim_end_matches(".jsonl")
            .trim_end_matches(".json")
            .rsplit('-')
            .next()
            .and_then(|v| v.parse().ok())
            .unwrap_or(0);
        result.push(OwnedFile {
            path: entry.path(),
            kind,
            bytes: metadata.len(),
            created_ms,
        });
    }
    result.sort_by(|a, b| {
        a.created_ms
            .cmp(&b.created_ms)
            .then_with(|| a.path.cmp(&b.path))
    });
    Ok(result)
}

pub(super) struct Journal {
    pub directory: PathBuf,
    pub process_session_id: String,
    pub identity: Value,
    pub limits: Limits,
    _lock: File,
    active: Option<(PathBuf, File, u64)>,
    active_created_ms: i64,
    sequence: u64,
    pub state: Value,
}
impl Journal {
    pub fn open(
        directory: PathBuf,
        session: String,
        source_identity: Value,
        limits: Limits,
    ) -> Result<Self, &'static str> {
        if !numeric_id(&session) {
            return Err("invalid-session");
        }
        if let Some(existing) = directory.ancestors().find(|path| path.exists()) {
            if !safe_directory(existing).map_err(|_| "directory-unreadable")? {
                return Err("unsafe-directory");
            }
        }
        fs::create_dir_all(&directory).map_err(|_| "directory-create-failed")?;
        if !safe_directory(&directory).map_err(|_| "directory-unreadable")? {
            return Err("unsafe-directory");
        }
        let lock_path = directory.join("writer.lock");
        if lock_path.exists() && !safe_regular(&lock_path).map_err(|_| "lock-unreadable")? {
            return Err("unsafe-file");
        }
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)
            .map_err(|_| "lock-open-failed")?;
        if !lock
            .try_lock_exclusive()
            .map_err(|_| "writer-unavailable")?
        {
            return Err("writer-unavailable");
        }
        let mut state = json!({"schemaVersion":1,"counters":{"dropped":0,"invalid":0,"rateLimited":0,"writeFailed":0,"evicted":0,"expired":0,"aggregated":0},"evictions":[],"metadataFolded":0});
        let state_path = directory.join("coverage-state.json");
        if state_path.exists() {
            state["metadataUnavailable"] = json!(true);
            if safe_regular(&state_path).unwrap_or(false) {
                if let Ok(file) = File::open(&state_path) {
                    let mut bytes = Vec::new();
                    if file
                        .take((STATE_BYTES + 1) as u64)
                        .read_to_end(&mut bytes)
                        .is_ok()
                        && bytes.len() <= STATE_BYTES
                    {
                        if let Ok(old) = serde_json::from_slice::<Value>(&bytes) {
                            // This file never enters a bundle verbatim. Export validates every field.
                            if old["schemaVersion"] == 1
                                && old["counters"].is_object()
                                && old["evictions"].as_array().is_some_and(|v| v.len() <= 256)
                            {
                                state["metadataUnavailable"] =
                                    json!(old["metadataUnavailable"].as_bool().unwrap_or(false));
                                for key in [
                                    "dropped",
                                    "invalid",
                                    "rateLimited",
                                    "writeFailed",
                                    "evicted",
                                    "expired",
                                    "aggregated",
                                    "frontendDropped",
                                ] {
                                    if let Some(n) = old["counters"][key].as_u64() {
                                        state["counters"][key] = json!(n);
                                    }
                                }
                                if let Some(n) = old["metadataFolded"].as_u64() {
                                    state["metadataFolded"] = json!(n);
                                }
                                // Reconstruct bounded, content-free eviction descriptors.
                                for item in old["evictions"].as_array().into_iter().flatten() {
                                    if matches!(item["kind"].as_str(), Some("expired" | "evicted"))
                                        && matches!(
                                            item["scope"].as_str(),
                                            Some("history" | "incident")
                                        )
                                        && item["bytes"].as_u64().is_some()
                                        && item["createdAtMs"].as_i64().is_some()
                                    {
                                        state["evictions"].as_array_mut().expect("owned state").push(json!({"kind":item["kind"],"scope":item["scope"],"bytes":item["bytes"],"createdAtMs":item["createdAtMs"]}));
                                    }
                                }
                            } else {
                                state["metadataUnavailable"] = json!(true);
                            }
                        }
                    }
                }
            } else {
                return Err("unsafe-file");
            }
        }
        state["normalEnd"] = json!(false);
        let mut journal = Self {
            directory,
            process_session_id: session,
            identity: identity(&source_identity),
            limits,
            _lock: lock,
            active: None,
            active_created_ms: 0,
            sequence: 0,
            state,
        };
        journal.reclaim_temps()?;
        journal.prune("history", 0, 0, Utc::now().timestamp_millis(), None)?;
        journal.prune("incident", 0, 0, Utc::now().timestamp_millis(), None)?;
        journal.persist_state(false)?;
        Ok(journal)
    }
    pub fn bump(&mut self, key: &str, count: u64) {
        let old = self.state["counters"][key].as_u64().unwrap_or(0);
        self.state["counters"][key] = json!(old.saturating_add(count));
    }
    fn eviction(&mut self, file: &OwnedFile, reason: &str) {
        self.bump(reason, 1);
        let list = self.state["evictions"].as_array_mut().expect("owned state");
        if list.len() >= 256 {
            list.remove(0);
            let folded = self.state["metadataFolded"].as_u64().unwrap_or(0);
            self.state["metadataFolded"] = json!(folded.saturating_add(1));
        }
        self.state["evictions"].as_array_mut().expect("owned state").push(json!({"kind":reason,"scope":file.kind,"bytes":file.bytes,"createdAtMs":file.created_ms}));
    }
    fn prune(
        &mut self,
        kind: &str,
        additional: u64,
        extra_count: usize,
        now: i64,
        protect: Option<&Path>,
    ) -> Result<(), &'static str> {
        let files = scan(&self.directory)?;
        let mut relevant = files
            .into_iter()
            .filter(|f| f.kind == kind)
            .collect::<Vec<_>>();
        if let Some((active_path, _, written_bytes)) = &self.active {
            if let Some(active) = relevant.iter_mut().find(|file| file.path == *active_path) {
                active.bytes = *written_bytes;
            }
        }
        let mut total = relevant.iter().map(|f| f.bytes).sum::<u64>();
        let mut count = relevant.len();
        let (budget, max_count, age) = if kind == "history" {
            (self.limits.history, 256, self.limits.history_age_ms)
        } else {
            (
                self.limits.incidents,
                self.limits.incident_count,
                self.limits.incident_age_ms,
            )
        };
        for file in relevant.drain(..) {
            let expired = now.saturating_sub(file.created_ms) > age;
            if file.created_ms > now {
                self.state["clockUncertain"] = json!(true);
            }
            if !expired
                && total.saturating_add(additional) <= budget
                && count.saturating_add(extra_count) <= max_count
            {
                continue;
            }
            if protect == Some(file.path.as_path()) {
                continue;
            }
            if !safe_regular(&file.path).map_err(|_| "reclaim-failed")? {
                return Err("unsafe-file");
            }
            fs::remove_file(&file.path).map_err(|_| "reclaim-failed")?;
            total = total.saturating_sub(file.bytes);
            count = count.saturating_sub(1);
            self.eviction(&file, if expired { "expired" } else { "evicted" });
        }
        if total.saturating_add(additional) > budget
            || count.saturating_add(extra_count) > max_count
        {
            return Err("capacity-unavailable");
        }
        Ok(())
    }
    fn new_segment(&mut self, now: i64, first: &Value) -> Result<(), &'static str> {
        self.flush(true)?;
        self.active = None;
        self.sequence = self.sequence.saturating_add(1);
        let name = format!(
            "history-{}-{}-{}.jsonl",
            self.process_session_id,
            self.sequence,
            now.max(0)
        );
        let path = self.directory.join(name);
        let header = json!({"schemaVersion":1,"kind":"segment-header","source":"collector","processSessionId":self.process_session_id,
            "identity":self.identity,"createdAtMs":now,"recordedAt":first["recordedAt"],"recordSeq":first["recordSeq"]});
        let mut bytes = serde_json::to_vec(&header).map_err(|_| "serialize-failed")?;
        bytes.push(b'\n');
        self.prune("history", bytes.len() as u64, 1, now, None)?;
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .read(true)
            .open(&path)
            .map_err(|_| "segment-create-failed")?;
        file.write_all(&bytes).map_err(|_| "write-failed")?;
        self.active = Some((path, file, bytes.len() as u64));
        self.active_created_ms = now;
        Ok(())
    }
    pub fn append(&mut self, event: &Value, now: i64) -> Result<(), &'static str> {
        let mut bytes = serde_json::to_vec(event).map_err(|_| "serialize-failed")?;
        bytes.push(b'\n');
        if bytes.len() as u64 > self.limits.segment {
            return Err("event-too-large");
        }
        if self.active.as_ref().map_or(true, |(_, _, size)| {
            size.saturating_add(bytes.len() as u64) > self.limits.segment
        }) || now.saturating_sub(self.active_created_ms) > self.limits.history_age_ms
        {
            self.new_segment(now, event)?;
        }
        let active_path = self.active.as_ref().map(|(path, _, _)| path.clone());
        self.prune(
            "history",
            bytes.len() as u64,
            0,
            now,
            active_path.as_deref(),
        )?;
        let Some((_, file, size)) = self.active.as_mut() else {
            return Err("writer-unavailable");
        };
        if file.write_all(&bytes).is_err() {
            self.active = None;
            return Err("write-failed");
        }
        *size += bytes.len() as u64;
        self.state["lastWritten"] = json!({"processSessionId":self.process_session_id,"recordSeq":event["recordSeq"],"recordedAt":event["recordedAt"]});
        Ok(())
    }
    pub fn write_incident(
        &mut self,
        name: &str,
        value: &Value,
        now: i64,
    ) -> Result<(), &'static str> {
        if file_kind(name) != Some("incident") {
            return Err("invalid-incident-name");
        }
        let created_ms = name
            .trim_end_matches(".json")
            .rsplit('-')
            .next()
            .and_then(|part| part.parse::<i64>().ok())
            .ok_or("invalid-incident-name")?;
        if now.saturating_sub(created_ms) > self.limits.incident_age_ms {
            self.prune("incident", 0, 0, now, None)?;
            return Err("incident-expired");
        }
        let bytes = serde_json::to_vec(value).map_err(|_| "serialize-failed")?;
        if bytes.len() > self.limits.incident_file {
            return Err("incident-too-large");
        }
        let path = self.directory.join(name);
        let old = if path.exists() {
            if !safe_regular(&path).unwrap_or(false) {
                return Err("unsafe-file");
            }
            path.metadata().map_err(|_| "file-unreadable")?.len()
        } else {
            0
        };
        self.prune(
            "incident",
            (bytes.len() as u64).saturating_sub(old),
            usize::from(old == 0),
            now,
            Some(&path),
        )?;
        self.atomic_write(&path, &bytes, true)
    }
    pub fn read_incident(&self, name: &str) -> Result<Value, &'static str> {
        let path = self.directory.join(name);
        if file_kind(name) != Some("incident") || !safe_regular(&path).unwrap_or(false) {
            return Err("incident-unavailable");
        }
        let mut bytes = Vec::new();
        File::open(path)
            .map_err(|_| "read-failed")?
            .take((self.limits.incident_file + 1) as u64)
            .read_to_end(&mut bytes)
            .map_err(|_| "read-failed")?;
        if bytes.len() > self.limits.incident_file {
            return Err("incident-too-large");
        }
        serde_json::from_slice(&bytes).map_err(|_| "read-failed")
    }
    pub fn flush(&mut self, sync: bool) -> Result<(), &'static str> {
        if let Some((_, file, _)) = self.active.as_mut() {
            file.flush().map_err(|_| "flush-failed")?;
            if sync {
                file.sync_data().map_err(|_| "sync-failed")?;
                self.state["lastSyncRequested"] = self.state["lastWritten"].clone();
            }
        }
        Ok(())
    }
    pub fn persist_state(&mut self, sync: bool) -> Result<(), &'static str> {
        self.state["processSessionId"] = json!(self.process_session_id);
        let bytes = serde_json::to_vec(&self.state).map_err(|_| "serialize-failed")?;
        if bytes.len() > STATE_BYTES {
            return Err("metadata-too-large");
        }
        self.atomic_write(&self.directory.join("coverage-state.json"), &bytes, sync)
    }
    fn reclaim_temps(&self) -> Result<(), &'static str> {
        for (count, entry) in fs::read_dir(&self.directory)
            .map_err(|_| "directory-unreadable")?
            .enumerate()
        {
            if count >= SCAN_FILES {
                return Err("scan-limit");
            }
            let entry = entry.map_err(|_| "directory-unreadable")?;
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue };
            if name.starts_with("pending-")
                && name.ends_with(".tmp")
                && numeric_id(&name[8..name.len() - 4])
            {
                if !safe_regular(&entry.path()).unwrap_or(false) {
                    return Err("unsafe-file");
                }
                fs::remove_file(entry.path()).map_err(|_| "reclaim-failed")?;
            }
        }
        Ok(())
    }
    fn atomic_write(&mut self, path: &Path, bytes: &[u8], sync: bool) -> Result<(), &'static str> {
        // A previous failure may have left a temporary file. Never accumulate retry files.
        self.reclaim_temps()?;
        if path.exists() && !safe_regular(path).unwrap_or(false) {
            return Err("unsafe-file");
        }
        self.sequence = self.sequence.saturating_add(1);
        let temporary = self.directory.join(format!(
            "pending-{}-{}.tmp",
            self.process_session_id, self.sequence
        ));
        // One state + one temporary are bounded well below the shared 1 MiB metadata reserve.
        if bytes.len() as u64 + STATE_BYTES as u64 > METADATA_BYTES {
            return Err("metadata-capacity");
        }
        let result = (|| {
            let mut file = OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&temporary)
                .map_err(|_| "temp-create-failed")?;
            file.write_all(bytes)
                .and_then(|_| file.flush())
                .map_err(|_| "write-failed")?;
            if sync {
                file.sync_data().map_err(|_| "sync-failed")?;
            }
            drop(file);
            atomic_replace(&temporary, path)
        })();
        if result.is_err() {
            let _ = fs::remove_file(temporary);
        }
        result
    }
}

#[cfg(not(windows))]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), &'static str> {
    fs::rename(source, destination).map_err(|_| "replace-failed")
}
#[cfg(windows)]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), &'static str> {
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
        .chain(Some(0))
        .collect::<Vec<_>>();
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    unsafe {
        MoveFileExW(
            PCWSTR(source.as_ptr()),
            PCWSTR(destination.as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    }
    .map_err(|_| "replace-failed")
}
