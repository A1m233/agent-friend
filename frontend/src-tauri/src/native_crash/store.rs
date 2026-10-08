use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};

pub(super) const MAX_DUMP_BYTES: u64 = 128 * 1024 * 1024;
const MAX_TOTAL_BYTES: u64 = 256 * 1024 * 1024;
const MAX_DUMPS: usize = 5;
const MAX_INCIDENTS: usize = 100;
const MAX_JSON_BYTES: u64 = 64 * 1024;

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct Session {
    pub schema_version: u32,
    pub session_id: String,
    pub process_id: u32,
    pub process_created_ticks: u64,
    pub recorded_at: String,
    pub build_identity: Option<crate::install_contract::BuildIdentity>,
    pub identity_status: String,
    pub capture_status: String,
    pub watcher_pid: Option<u32>,
    pub watcher_created_ticks: Option<u64>,
    pub completed: bool,
    pub failures: Vec<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct Dump {
    pub status: String,
    pub size_bytes: Option<u64>,
    pub sha256: Option<String>,
    pub error_code: Option<String>,
}
impl Dump {
    pub fn unavailable(code: &str) -> Self {
        Self {
            status: "not-captured".into(),
            size_bytes: None,
            sha256: None,
            error_code: Some(code.into()),
        }
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct Incident {
    pub schema_version: u32,
    pub incident_id: String,
    pub session_id: String,
    pub recorded_at: String,
    pub process_id: u32,
    pub process_created_ticks: u64,
    pub build_identity: Option<crate::install_contract::BuildIdentity>,
    pub identity_status: String,
    pub kind: String,
    pub thread_id: Option<u32>,
    pub exception_code: Option<String>,
    pub exception_address: Option<String>,
    pub fault_module: Option<String>,
    pub fault_rva: Option<String>,
    pub exit_code: Option<u32>,
    pub exit_time_ticks: Option<u64>,
    pub orderly_reason: Option<String>,
    pub reentrant_count: u32,
    pub services: Vec<ServiceExit>,
    pub dump: Dump,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct ServiceExit {
    pub role: String,
    pub generation: u64,
    pub process_id: u32,
    pub process_created_ticks: u64,
    pub exit_code: Option<u32>,
    pub exit_time_ticks: Option<u64>,
    pub order: String,
}

impl Incident {
    pub(super) fn new(session: &Session, sequence: u32, kind: &str) -> Self {
        Self {
            schema_version: 1,
            incident_id: format!("{}-{sequence:06}", session.session_id),
            session_id: session.session_id.clone(),
            recorded_at: now(),
            process_id: session.process_id,
            process_created_ticks: session.process_created_ticks,
            build_identity: session.build_identity.clone(),
            identity_status: session.identity_status.clone(),
            kind: kind.into(),
            thread_id: None,
            exception_code: None,
            exception_address: None,
            fault_module: None,
            fault_rva: None,
            exit_code: None,
            exit_time_ticks: None,
            orderly_reason: None,
            reentrant_count: 0,
            services: vec![],
            dump: Dump::unavailable("no-exception-context"),
        }
    }
}

pub(crate) struct ExportDump {
    pub file: File,
    pub archive_name: String,
    pub size_bytes: u64,
    pub sha256: String,
}
pub(crate) struct NativeCrashExport {
    pub coverage: Value,
    pub incidents: Vec<(String, Value)>,
    pub dumps: Vec<ExportDump>,
    pub failures: Vec<String>,
}

pub(super) fn now() -> String {
    Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}
pub(super) fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 96 && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
}
pub(super) fn initialize(root: &Path) -> Result<(), String> {
    for folder in ["sessions", "incidents", "dumps"] {
        fs::create_dir_all(root.join(folder)).map_err(|_| "storage-unwritable".to_string())?;
        if fs::symlink_metadata(root.join(folder))
            .map_err(|_| "storage-unreadable")?
            .file_type()
            .is_symlink()
        {
            return Err("storage-symlink-rejected".into());
        }
    }
    Ok(())
}
pub(super) fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let bytes = serde_json::to_vec(value).map_err(|_| "metadata-encode-failed")?;
    if bytes.len() as u64 > MAX_JSON_BYTES {
        return Err("metadata-size-limit".into());
    }
    let temp = path.with_extension(format!("{}.partial", std::process::id()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|_| "metadata-create-failed")?;
    let result = file
        .write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| "metadata-write-failed".to_string());
    drop(file);
    if let Err(error) = result {
        let _ = fs::remove_file(&temp);
        return Err(error);
    }
    #[cfg(windows)]
    let result = super::windows::replace_file(&temp, path);
    #[cfg(not(windows))]
    let result = fs::rename(&temp, path).map_err(|_| "metadata-rename-failed".to_string());
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}
pub(super) fn save_session(root: &Path, value: &Session) -> Result<(), String> {
    if !valid_id(&value.session_id) {
        return Err("invalid-session-id".into());
    }
    write_json(
        &root
            .join("sessions")
            .join(format!("{}.json", value.session_id)),
        value,
    )
}
pub(super) fn save_incident(root: &Path, value: &Incident) -> Result<(), String> {
    if !valid_id(&value.incident_id) {
        return Err("invalid-incident-id".into());
    }
    write_json(
        &root
            .join("incidents")
            .join(format!("{}.json", value.incident_id)),
        value,
    )
}
pub(super) fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, String> {
    if fs::symlink_metadata(path)
        .map_err(|_| "metadata-missing")?
        .file_type()
        .is_symlink()
    {
        return Err("metadata-symlink-rejected".into());
    }
    let mut file = File::open(path).map_err(|_| "metadata-unreadable")?;
    #[cfg(windows)]
    super::windows::validate_file_parent(
        &file,
        &path
            .parent()
            .ok_or("metadata-parent-missing")?
            .canonicalize()
            .map_err(|_| "metadata-parent-missing")?,
    )?;
    if file.metadata().map_err(|_| "metadata-unreadable")?.len() > MAX_JSON_BYTES {
        return Err("metadata-size-limit".into());
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_JSON_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "metadata-unreadable")?;
    if bytes.len() as u64 > MAX_JSON_BYTES {
        return Err("metadata-size-limit".into());
    }
    serde_json::from_slice(&bytes).map_err(|_| "metadata-corrupt".into())
}

fn json_files(root: &Path, folder: &str) -> Result<Vec<PathBuf>, String> {
    let dir = root.join(folder);
    if !dir.exists() {
        return Ok(vec![]);
    }
    if fs::symlink_metadata(&dir)
        .map_err(|_| "evidence-directory-unreadable")?
        .file_type()
        .is_symlink()
    {
        return Err("evidence-directory-symlink-rejected".into());
    }
    let mut files = Vec::new();
    for (index, entry) in fs::read_dir(dir)
        .map_err(|_| "evidence-directory-unreadable")?
        .take(2049)
        .enumerate()
    {
        if index == 2048 {
            return Err("evidence-scan-limit".into());
        }
        let entry = entry.map_err(|_| "evidence-entry-unreadable")?;
        let path = entry.path();
        if path.extension().is_some_and(|e| e == "json")
            && path
                .file_stem()
                .and_then(|s| s.to_str())
                .is_some_and(valid_id)
        {
            files.push(path);
        }
    }
    files.sort();
    Ok(files)
}

pub(super) fn sessions(root: &Path) -> Vec<(PathBuf, Result<Session, String>)> {
    json_files(root, "sessions")
        .unwrap_or_default()
        .into_iter()
        .map(|p| {
            let value = read_json(&p);
            (p, value)
        })
        .collect()
}

/// Called only after both the recorded process and its observer are confirmed dead.
pub(super) fn recover_interrupted(root: &Path, session_id: &str) -> Vec<String> {
    let mut errors = Vec::new();
    let files = match json_files(root, "incidents") {
        Ok(v) => v,
        Err(e) => return vec![e],
    };
    for path in files {
        let Ok(mut incident) = read_json::<Incident>(&path) else {
            continue;
        };
        if incident.session_id != session_id
            || !valid_id(&incident.incident_id)
            || path.file_stem().and_then(|v| v.to_str()) != Some(&incident.incident_id)
            || incident.dump.status != "pending"
        {
            continue;
        }
        incident.dump.status = "failed".into();
        incident.dump.error_code = Some("capture-interrupted".into());
        if let Err(e) = save_incident(root, &incident) {
            errors.push(e);
        }
        for extension in ["partial", "dmp"] {
            let path = root
                .join("dumps")
                .join(format!("{}.{}", incident.incident_id, extension));
            if let Err(e) = fs::remove_file(path) {
                if e.kind() != std::io::ErrorKind::NotFound {
                    errors.push("retention-delete-failed".into());
                }
            }
        }
    }
    errors
}

pub(super) fn hash_file(file: &mut File) -> Result<(u64, String), String> {
    file.seek(SeekFrom::Start(0))
        .map_err(|_| "dump-seek-failed")?;
    let mut hash = Sha256::new();
    let mut bytes = [0; 65536];
    let mut size = 0;
    loop {
        let n = file.read(&mut bytes).map_err(|_| "dump-read-failed")?;
        if n == 0 {
            break;
        }
        size += n as u64;
        if size > MAX_DUMP_BYTES {
            return Err("dump-size-limit".into());
        }
        hash.update(&bytes[..n]);
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| "dump-seek-failed")?;
    Ok((size, format!("{:x}", hash.finalize())))
}

pub(super) fn completed_dump(root: &Path, incident: &Incident) -> Result<ExportDump, String> {
    if !valid_id(&incident.incident_id) {
        return Err("invalid-incident-id".into());
    }
    let path = root
        .join("dumps")
        .join(format!("{}.dmp", incident.incident_id));
    if fs::symlink_metadata(&path)
        .map_err(|_| "dump-missing")?
        .file_type()
        .is_symlink()
    {
        return Err("dump-symlink-rejected".into());
    }
    let canonical_dir = root
        .join("dumps")
        .canonicalize()
        .map_err(|_| "dump-directory-missing")?;
    if path.canonicalize().map_err(|_| "dump-missing")?.parent() != Some(canonical_dir.as_path()) {
        return Err("dump-path-rejected".into());
    }
    let mut file = File::open(&path).map_err(|_| "dump-unreadable")?;
    #[cfg(windows)]
    super::windows::validate_file_parent(&file, &canonical_dir)?;
    let mut magic = [0; 4];
    file.read_exact(&mut magic).map_err(|_| "dump-corrupt")?;
    if &magic != b"MDMP" {
        return Err("dump-corrupt".into());
    }
    let (size_bytes, sha256) = hash_file(&mut file)?;
    if incident.dump.size_bytes != Some(size_bytes)
        || incident.dump.sha256.as_deref() != Some(&sha256)
    {
        return Err("dump-integrity-mismatch".into());
    }
    Ok(ExportDump {
        file,
        archive_name: format!(
            "diagnostics/native-crash/dumps/{}.dmp",
            incident.incident_id
        ),
        size_bytes,
        sha256,
    })
}

pub(super) fn export(root: &Path, include_raw: bool) -> NativeCrashExport {
    let mut failures = Vec::new();
    let mut incidents = Vec::new();
    let mut dumps = Vec::new();
    let mut inspected_dumps = 0;
    let mut inspected_bytes = 0_u64;
    let mut states = Vec::new();
    let session_paths = match json_files(root, "sessions") {
        Ok(v) => v,
        Err(e) => {
            failures.push(e);
            vec![]
        }
    };
    let mut session_values: Vec<_> = session_paths
        .into_iter()
        .map(|p| {
            let value = read_json::<Session>(&p);
            (p, value)
        })
        .collect();
    session_values.sort_by_key(|(_, v)| {
        v.as_ref()
            .map(|s| s.recorded_at.clone())
            .unwrap_or_default()
    });
    if session_values.len() > MAX_INCIDENTS {
        failures.push("session-export-limit".into());
    }
    for (path, session) in session_values.into_iter().rev().take(MAX_INCIDENTS) {
        match session {
            Ok(mut s)
                if valid_id(&s.session_id)
                    && path.file_stem().and_then(|n| n.to_str()) == Some(&s.session_id) =>
            {
                s.failures = s
                    .failures
                    .iter()
                    .take(32)
                    .map(|e| safe_error(e).to_string())
                    .collect();
                failures.extend(s.failures.clone());
                let status = if matches!(
                    s.capture_status.as_str(),
                    "starting" | "ready" | "unavailable" | "degraded"
                ) {
                    s.capture_status.as_str()
                } else {
                    "unavailable"
                };
                states.push(json!({"sessionId":s.session_id,"captureStatus":status,"completed":s.completed,"failures":s.failures}));
            }
            Ok(_) => failures.push("session-identity-invalid".into()),
            Err(e) => failures.push(e),
        }
    }
    let paths = match json_files(root, "incidents") {
        Ok(paths) => paths,
        Err(e) => {
            failures.push(e);
            vec![]
        }
    };
    let mut incident_values: Vec<_> = paths
        .into_iter()
        .map(|p| {
            let value = read_json::<Incident>(&p);
            (p, value)
        })
        .collect();
    incident_values.sort_by_key(|(_, v)| {
        v.as_ref()
            .map(|s| s.recorded_at.clone())
            .unwrap_or_default()
    });
    if incident_values.len() > MAX_INCIDENTS {
        failures.push("incident-export-limit".into());
    }
    for (path, result) in incident_values.into_iter().rev().take(MAX_INCIDENTS) {
        let mut incident: Incident = match result {
            Ok(v) => v,
            Err(e) => {
                failures.push(e);
                continue;
            }
        };
        if incident.schema_version != 1
            || !valid_id(&incident.incident_id)
            || path.file_stem().and_then(|s| s.to_str()) != Some(&incident.incident_id)
        {
            failures.push("incident-identity-invalid".into());
            continue;
        }
        if let Err(e) = sanitize(&mut incident) {
            failures.push(e.into());
            continue;
        }
        let mut value = serde_json::to_value(&incident).unwrap_or(Value::Null);
        let mut status = incident.dump.status.clone();
        if status == "failed" || status == "pending" {
            failures.push(
                incident
                    .dump
                    .error_code
                    .clone()
                    .unwrap_or_else(|| "dump-incomplete".into()),
            );
        }
        let mut archive_entry = Value::Null;
        if status == "captured" {
            let expected_bytes = incident.dump.size_bytes.unwrap_or(MAX_DUMP_BYTES + 1);
            if inspected_dumps >= MAX_DUMPS
                || inspected_bytes.saturating_add(expected_bytes) > MAX_TOTAL_BYTES
            {
                status = "excluded".into();
                value["dump"]["errorCode"] = json!("export-budget-exceeded");
                failures.push("export-budget-exceeded".into());
            } else {
                match completed_dump(root, &incident) {
                    Ok(dump) if include_raw => {
                        status = "included".into();
                        archive_entry = Value::String(dump.archive_name.clone());
                        dumps.push(dump);
                    }
                    Ok(_) => status = "excluded".into(),
                    Err(e) => {
                        status = if e == "dump-missing" {
                            "missing"
                        } else {
                            "corrupt"
                        }
                        .into();
                        failures.push(e);
                    }
                };
                inspected_dumps += 1;
                inspected_bytes = inspected_bytes.saturating_add(expected_bytes);
            }
        }
        value["dump"]["status"] = Value::String(status);
        value["dump"]["archiveEntry"] = archive_entry;
        incidents.push((
            format!(
                "diagnostics/native-crash/incidents/{}.json",
                incident.incident_id
            ),
            value,
        ));
    }
    failures.sort();
    failures.dedup();
    let coverage = json!({"schemaVersion":1,"platform":std::env::consts::OS,
        "captureStatus":if cfg!(windows) { "per-session" } else { "unsupported-platform" },
        "nativeExceptionCapture":if cfg!(windows){"unhandled-filter-and-stack-overflow-observer"}else{"unsupported-platform"}, "panicCapture":if cfg!(windows){"preallocated-context-handoff"}else{"unsupported-platform"},
        "fastFailCapture":if cfg!(windows){"exit-only"}else{"unsupported-platform"},"forcedTerminationCapture":if cfg!(windows){"exit-only"}else{"unsupported-platform"}, "rawDumpsRequested":include_raw,
        "incidentCount":incidents.len(),"rawDumpCount":dumps.len(),"sessions":states,"failures":failures,
        "limits":{"maxDumpBytes":MAX_DUMP_BYTES,"maxTotalDumpBytes":MAX_TOTAL_BYTES,"maxDumps":MAX_DUMPS,"dumpDays":14,"maxIncidents":MAX_INCIDENTS,"incidentDays":30}});
    NativeCrashExport {
        coverage,
        incidents,
        dumps,
        failures,
    }
}

fn safe_error(value: &str) -> &str {
    match value {
        "no-exception-context"
        | "orderly-exit"
        | "exit-only-no-exception-context"
        | "retention-policy"
        | "storage-unwritable"
        | "storage-unreadable"
        | "storage-symlink-rejected"
        | "storage-path-unavailable"
        | "metadata-encode-failed"
        | "metadata-size-limit"
        | "metadata-create-failed"
        | "metadata-write-failed"
        | "metadata-rename-failed"
        | "watcher-handshake-failed"
        | "stack-overflow-handler-unavailable"
        | "stack-guarantee-unavailable"
        | "ipc-metadata-invalid"
        | "ipc-size-limit"
        | "ipc-mapping-create"
        | "ipc-event-create"
        | "ipc-target-open"
        | "ipc-map-view"
        | "ipc-map-alignment"
        | "helper-image-missing"
        | "helper-handle-inherit"
        | "helper-attribute-list"
        | "helper-handle-list"
        | "helper-start-failed"
        | "evidence-registration-limit-or-failure"
        | "service-evidence-write-failed"
        | "observer-wait-failed"
        | "exit-evidence-write-failed"
        | "observer-and-process-ended-without-result"
        | "retention-delete-failed"
        | "session-retention-delete-failed"
        | "retention-reservation-failed"
        | "capture-interrupted"
        | "handoff-timeout"
        | "handoff-wait-failed"
        | "handoff-watcher-exited"
        | "handoff-protocol-failed"
        | "handoff-notify-failed"
        | "capture-context-expired"
        | "retention-directory-invalid"
        | "dump-timeout"
        | "dump-size-limit"
        | "dump-write-failed"
        | "dump-generation-failed"
        | "dump-worker-unavailable" => value,
        _ => "unrecognized-evidence-error",
    }
}
fn hex(value: &str, max: usize) -> bool {
    value.starts_with("0x")
        && value.len() > 2
        && value.len() <= max
        && value[2..].bytes().all(|b| b.is_ascii_hexdigit())
}
fn slug(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'_' | b'+'))
}
fn sanitize(i: &mut Incident) -> Result<(), &'static str> {
    if !valid_id(&i.session_id)
        || DateTime::parse_from_rfc3339(&i.recorded_at).is_err()
        || !matches!(
            i.kind.as_str(),
            "native-exception"
                | "stack-overflow-observed"
                | "rust-panic"
                | "process-exit"
                | "service-exit"
                | "unexpected-end-unknown"
        )
    {
        return Err("incident-schema-invalid");
    }
    if !matches!(
        i.identity_status.as_str(),
        "embedded" | "development" | "invalid"
    ) {
        i.identity_status = "invalid".into();
    }
    if i.build_identity.as_ref().is_some_and(|b| {
        !slug(&b.product_version)
            || !slug(&b.build_id)
            || !slug(&b.profile_id)
            || !slug(&b.distribution)
            || !matches!(
                b.build_mode.as_str(),
                "debug" | "release" | "formal" | "local-test"
            )
            || b.source_commit.len() != 40
            || !b.source_commit.bytes().all(|v| v.is_ascii_hexdigit())
            || b.source_tree_sha256.len() != 64
            || !b.source_tree_sha256.bytes().all(|v| v.is_ascii_hexdigit())
    }) {
        i.build_identity = None;
        i.identity_status = "invalid".into();
    }
    if i.exception_code.as_deref().is_some_and(|v| !hex(v, 10))
        || i.exception_address.as_deref().is_some_and(|v| !hex(v, 18))
        || i.fault_rva.as_deref().is_some_and(|v| !hex(v, 18))
    {
        return Err("incident-address-invalid");
    }
    if i.fault_module
        .as_deref()
        .is_some_and(|v| !slug(v) || v == "." || v == "..")
    {
        i.fault_module = None;
    }
    if i.orderly_reason.as_deref().is_some_and(|v| {
        !matches!(
            v,
            "normal-return" | "tauri-exit" | "second-instance" | "startup-rejected"
        )
    }) {
        i.orderly_reason = None;
    }
    if !matches!(
        i.dump.status.as_str(),
        "pending" | "captured" | "not-captured" | "failed" | "expired"
    ) {
        return Err("incident-dump-status-invalid");
    }
    if i.dump
        .sha256
        .as_deref()
        .is_some_and(|v| v.len() != 64 || !v.bytes().all(|b| b.is_ascii_hexdigit()))
    {
        return Err("incident-dump-hash-invalid");
    }
    i.dump.error_code = i
        .dump
        .error_code
        .as_deref()
        .map(|e| safe_error(e).to_string());
    i.services.truncate(100);
    for s in &mut i.services {
        if !matches!(s.role.as_str(), "bridge" | "voice") {
            s.role = "unknown".into();
        }
        if !matches!(
            s.order.as_str(),
            "service-first" | "shell-first" | "simultaneous" | "order-unknown"
        ) {
            s.order = "order-unknown".into();
        }
    }
    Ok(())
}

/// Reserve enough space for one maximum-size dump. Only completed, known records are pruned.
pub(super) fn prune(root: &Path, reserve_dump: bool) -> Vec<String> {
    if ["incidents", "sessions", "dumps"].iter().any(|p| {
        fs::symlink_metadata(root.join(p))
            .map_or(true, |m| !m.is_dir() || m.file_type().is_symlink())
    }) {
        return vec!["retention-directory-invalid".into()];
    }
    let mut errors = Vec::new();
    let paths = match json_files(root, "incidents") {
        Ok(v) => v,
        Err(e) => return vec![e],
    };
    let mut values: Vec<_> = paths
        .into_iter()
        .filter_map(|p| read_json::<Incident>(&p).ok().map(|v| (p, v)))
        .collect();
    values.sort_by(|a, b| a.1.recorded_at.cmp(&b.1.recorded_at));
    let mut count = values
        .iter()
        .filter(|(_, v)| v.dump.status == "captured")
        .count();
    let mut total: u64 = values
        .iter()
        .filter(|(_, v)| v.dump.status == "captured")
        .map(|(_, v)| v.dump.size_bytes.unwrap_or(0))
        .fold(0_u64, u64::saturating_add);
    let mut remaining = values.len();
    for (path, mut value) in values {
        if !valid_id(&value.incident_id)
            || path.file_stem().and_then(|s| s.to_str()) != Some(&value.incident_id)
            || value.dump.status == "pending"
        {
            continue;
        }
        if value
            .dump
            .size_bytes
            .is_some_and(|size| size > MAX_DUMP_BYTES)
        {
            errors.push("retention-metadata-size-invalid".into());
            continue;
        }
        let age = DateTime::parse_from_rfc3339(&value.recorded_at)
            .map(|t| Utc::now().signed_duration_since(t).num_days())
            .unwrap_or(0);
        let remove_record = age > 30 || remaining > MAX_INCIDENTS;
        if value.dump.status == "captured"
            && (age > 14
                || count + usize::from(reserve_dump) > MAX_DUMPS
                || total.saturating_add(if reserve_dump { MAX_DUMP_BYTES } else { 0 })
                    > MAX_TOTAL_BYTES
                || remove_record)
        {
            let dump_path = root
                .join("dumps")
                .join(format!("{}.dmp", value.incident_id));
            match fs::remove_file(dump_path) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => {
                    errors.push("retention-delete-failed".into());
                    continue;
                }
            }
            count = count.saturating_sub(1);
            total = total.saturating_sub(value.dump.size_bytes.unwrap_or(0));
            value.dump.status = "expired".into();
            value.dump.error_code = Some("retention-policy".into());
            if let Err(e) = save_incident(root, &value) {
                errors.push(e);
            }
        }
        if remove_record {
            if fs::remove_file(path).is_err() {
                errors.push("retention-delete-failed".into());
            } else {
                remaining = remaining.saturating_sub(1);
            }
        }
    }
    // Session records contain no memory; retain at most 100 completed records for 30 days.
    let mut session_records = sessions(root);
    session_records.sort_by_key(|(_, v)| {
        v.as_ref()
            .map(|s| s.recorded_at.clone())
            .unwrap_or_default()
    });
    let mut session_count = session_records.len();
    for (path, value) in session_records {
        if let Ok(session) = value {
            let age = DateTime::parse_from_rfc3339(&session.recorded_at)
                .map(|t| Utc::now().signed_duration_since(t).num_days())
                .unwrap_or(0);
            if session.completed && (age > 30 || session_count > MAX_INCIDENTS) {
                if fs::remove_file(path).is_err() {
                    errors.push("session-retention-delete-failed".into());
                } else {
                    session_count -= 1;
                }
            }
        }
    }
    errors
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (PathBuf, Session) {
        let root = std::env::temp_dir().join(format!(
            "af-native-store-{}-{}",
            std::process::id(),
            rand::random::<u64>()
        ));
        initialize(&root).unwrap();
        let s = Session {
            schema_version: 1,
            session_id: "12-123-abc".into(),
            process_id: 12,
            process_created_ticks: 123,
            recorded_at: now(),
            build_identity: None,
            identity_status: "development".into(),
            capture_status: "ready".into(),
            watcher_pid: None,
            watcher_created_ticks: None,
            completed: true,
            failures: vec![],
        };
        (root, s)
    }
    #[test]
    fn export_requires_opt_in_and_revalidates_integrity() {
        let (root, s) = fixture();
        let mut i = Incident::new(&s, 1, "rust-panic");
        let p = root.join("dumps").join(format!("{}.dmp", i.incident_id));
        fs::write(&p, b"MDMPfixture private bytes").unwrap();
        let (size, hash) = hash_file(&mut File::open(&p).unwrap()).unwrap();
        i.dump = Dump {
            status: "captured".into(),
            size_bytes: Some(size),
            sha256: Some(hash),
            error_code: None,
        };
        save_incident(&root, &i).unwrap();
        let e = export(&root, false);
        assert!(e.dumps.is_empty());
        assert_eq!(e.incidents[0].1["dump"]["status"], "excluded");
        assert_eq!(export(&root, true).dumps.len(), 1);
        fs::write(p, b"MDMPmodified").unwrap();
        let e = export(&root, true);
        assert!(e.dumps.is_empty());
        assert!(e.failures.contains(&"dump-integrity-mismatch".into()));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn retention_keeps_summaries_but_caps_dumps() {
        let (root, s) = fixture();
        for n in 1..=7 {
            let mut i = Incident::new(&s, n, "native-exception");
            let p = root.join("dumps").join(format!("{}.dmp", i.incident_id));
            fs::write(&p, b"MDMPdata").unwrap();
            let (size, hash) = hash_file(&mut File::open(&p).unwrap()).unwrap();
            i.dump = Dump {
                status: "captured".into(),
                size_bytes: Some(size),
                sha256: Some(hash),
                error_code: None,
            };
            save_incident(&root, &i).unwrap();
        }
        assert!(prune(&root, false).is_empty());
        let e = export(&root, true);
        assert_eq!(e.incidents.len(), 7);
        assert_eq!(e.dumps.len(), 5);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn rejects_unsafe_ids_and_malformed_metadata() {
        assert!(!valid_id("../outside"));
        assert!(!valid_id("a/b"));
        let (root, s) = fixture();
        let mut i = Incident::new(&s, 1, "native-exception");
        i.incident_id = "../bad".into();
        assert!(save_incident(&root, &i).is_err());
        fs::write(root.join("incidents/corrupt.json"), "invalid").unwrap();
        assert!(export(&root, false)
            .failures
            .contains(&"metadata-corrupt".into()));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn installed_build_identity_and_dump_failures_survive_export() {
        let (root, mut session) = fixture();
        for (number, mode) in [(1, "formal"), (2, "local-test")] {
            session.build_identity = Some(crate::install_contract::BuildIdentity {
                schema_version: 1,
                product_version: "0.4.0".into(),
                build_id: "20261008T000000Z-abc123-fixture".into(),
                source_commit: "a".repeat(40),
                source_tree_sha256: "b".repeat(64),
                dirty: false,
                build_mode: mode.into(),
                distribution: "ordinary".into(),
                profile_id: "production".into(),
            });
            session.identity_status = "embedded".into();
            let mut incident = Incident::new(&session, number, "native-exception");
            incident.dump.status = "failed".into();
            incident.dump.error_code = Some("dump-timeout".into());
            save_incident(&root, &incident).unwrap();
        }
        let export = export(&root, false);
        assert_eq!(export.incidents.len(), 2);
        assert!(export
            .incidents
            .iter()
            .all(|(_, v)| v["buildIdentity"]["buildId"] == "20261008T000000Z-abc123-fixture"));
        assert!(export.failures.contains(&"dump-timeout".into()));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn disk_metadata_cannot_inject_payload_or_paths_into_default_summary() {
        let (root, session) = fixture();
        let mut incident = Incident::new(&session, 1, "rust-panic");
        incident.fault_module = Some("C:\\private\\token-secret.dll".into());
        incident.orderly_reason = Some("arbitrary private message".into());
        incident.dump.error_code = Some("private payload in error".into());
        save_incident(&root, &incident).unwrap();
        let export = export(&root, false);
        let text = serde_json::to_string(&export.incidents[0].1).unwrap();
        assert!(!text.contains("private"));
        assert!(!text.contains("secret"));
        assert_eq!(
            export.incidents[0].1["dump"]["errorCode"],
            "unrecognized-evidence-error"
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn corrupt_retention_sizes_cannot_overflow_and_scan_limit_is_visible() {
        let (root, session) = fixture();
        for sequence in [1, 2] {
            let mut incident = Incident::new(&session, sequence, "native-exception");
            incident.dump.status = "captured".into();
            incident.dump.size_bytes = Some(u64::MAX);
            save_incident(&root, &incident).unwrap();
        }
        assert!(prune(&root, false).contains(&"retention-metadata-size-invalid".into()));
        for index in 0..2049 {
            fs::write(
                root.join("sessions").join(format!("scan-{index}.json")),
                b"{}",
            )
            .unwrap();
        }
        assert!(export(&root, false)
            .failures
            .contains(&"evidence-scan-limit".into()));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn event_selection_uses_recorded_time_instead_of_pid_filename() {
        let (root, session) = fixture();
        let mut older = Incident::new(&session, 1, "rust-panic");
        older.incident_id = "zzz-older".into();
        older.recorded_at = (Utc::now() - chrono::Duration::minutes(1)).to_rfc3339();
        let mut newer = Incident::new(&session, 2, "rust-panic");
        newer.incident_id = "aaa-newer".into();
        save_incident(&root, &older).unwrap();
        save_incident(&root, &newer).unwrap();
        let result = export(&root, false);
        assert_eq!(result.incidents[0].1["incidentId"], "aaa-newer");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn abandoned_pending_capture_becomes_retirable_and_drops_partial() {
        let (root, session) = fixture();
        let mut incident = Incident::new(&session, 1, "native-exception");
        incident.dump.status = "pending".into();
        save_incident(&root, &incident).unwrap();
        let partial = root
            .join("dumps")
            .join(format!("{}.partial", incident.incident_id));
        fs::write(&partial, b"unfinished").unwrap();
        assert!(recover_interrupted(&root, &session.session_id).is_empty());
        assert!(!partial.exists());
        let result = export(&root, false);
        assert_eq!(result.incidents[0].1["dump"]["status"], "failed");
        assert!(result.failures.contains(&"capture-interrupted".into()));
        fs::remove_dir_all(root).unwrap();
    }
}
