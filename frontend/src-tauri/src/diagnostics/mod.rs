use crate::{paths, runtime_config, runtime_services};
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs::{self, File},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager, State};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipWriter};

const MAX_DESCRIPTION_CHARS: usize = 20_000;
const MAX_SESSIONS: usize = 20;
const MAX_LOG_FILE_BYTES: u64 = 5 * 1024 * 1024;
const MAX_LOG_TOTAL_BYTES: u64 = 20 * 1024 * 1024;
const PACKAGE_BUILD_METADATA: &str = "package-build.json";

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerateDiagnosticBundleRequest {
    description: Option<String>,
    session_ids: Vec<String>,
    locale: Option<String>,
    timezone: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CollectionFailure {
    collection: String,
    item: Option<String>,
    message: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerateDiagnosticBundleResult {
    path: String,
    outcome: &'static str,
    included_session_ids: Vec<String>,
    failures: Vec<CollectionFailure>,
}

struct ArchiveEntry {
    name: String,
    bytes: Vec<u8>,
}

#[tauri::command]
pub async fn generate_diagnostic_bundle(
    app: AppHandle,
    request: GenerateDiagnosticBundleRequest,
    services: State<'_, Arc<runtime_services::RuntimeServiceSupervisor>>,
) -> Result<GenerateDiagnosticBundleResult, String> {
    let description = normalize_description(request.description)?;
    let session_ids = normalize_session_ids(request.session_ids)?;
    let generated_at = Local::now();
    let credentials = runtime_config::credential_redaction_values()
        .map_err(|e| format!("credential protection unavailable: {e}"))?;
    let home = dirs::home_dir();

    let bridge_snapshot = services
        .snapshot(runtime_services::RuntimeServiceKind::Bridge)
        .await;
    let voice_snapshot = services
        .snapshot(runtime_services::RuntimeServiceKind::Voice)
        .await;

    let mut failures = Vec::new();
    let runtime_config = match runtime_config::get_runtime_config_status() {
        Ok(snapshot) => serde_json::to_value(snapshot).map_err(|e| e.to_string())?,
        Err(error) => {
            failures.push(CollectionFailure {
                collection: "runtime-config".into(),
                item: None,
                message: redact_text(&error, &credentials, home.as_deref()).0,
            });
            json!({"status": "failed"})
        }
    };

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| format!("create local diagnostics client failed: {e}"))?;
    let session_results = futures_util::future::join_all(session_ids.iter().map(|session_id| {
        let client = client.clone();
        let session_id = session_id.clone();
        async move {
            let mut url =
                reqwest::Url::parse(runtime_services::RuntimeServiceKind::Bridge.base_url())
                    .map_err(|e| e.to_string())?;
            url.path_segments_mut()
                .map_err(|_| "bridge base URL cannot accept path segments".to_string())?
                .extend(["v1", "sessions", &session_id]);
            let response = client.get(url).send().await.map_err(|e| e.to_string())?;
            if !response.status().is_success() {
                return Err(format!("bridge returned HTTP {}", response.status()));
            }
            let mut value = response.json::<Value>().await.map_err(|e| e.to_string())?;
            if !value.get("events").is_some_and(Value::is_array) {
                return Err("session response is missing events".into());
            }
            if let Some(object) = value.as_object_mut() {
                object.remove("active_events");
            }
            Ok::<_, String>((session_id, value))
        }
    }))
    .await;

    let mut included_session_ids = Vec::new();
    let mut entries = Vec::new();
    if let Some(description) = description {
        let (description, _) = redact_text(&description, &credentials, home.as_deref());
        entries.push(ArchiveEntry {
            name: "problem.txt".into(),
            bytes: description.into_bytes(),
        });
    }

    let resource_dir =
        tauri::utils::platform::resource_dir(app.package_info(), &tauri::Env::default()).ok();
    let app_info = json!({
        "product": app.package_info().name,
        "version": app.package_info().version.to_string(),
        "buildMode": if cfg!(debug_assertions) { "debug" } else { "release" },
        "packageBuild": load_package_build_metadata(resource_dir.as_deref()),
        "generatedAt": generated_at.to_rfc3339(),
    });
    let os = os_info::get();
    let environment = json!({
        "osType": os.os_type().to_string(),
        "osVersion": os.version().to_string(),
        "architecture": std::env::consts::ARCH,
        "locale": request.locale,
        "timezone": request.timezone,
    });
    let runtime_services = json!({"bridge": bridge_snapshot, "voice": voice_snapshot});

    for (name, mut value) in [
        ("diagnostics/application.json", app_info),
        ("diagnostics/environment.json", environment),
        ("diagnostics/runtime-services.json", runtime_services),
        ("diagnostics/runtime-config.json", runtime_config),
    ] {
        let mut count = 0;
        redact_json(&mut value, &credentials, home.as_deref(), &mut count);
        entries.push(json_entry(name, &value)?);
    }

    for (index, result) in session_results.into_iter().enumerate() {
        match result {
            Ok((session_id, mut value)) => {
                let mut count = 0;
                redact_json(&mut value, &credentials, home.as_deref(), &mut count);
                let entry_name = format!("sessions/session-{:03}.json", index + 1);
                entries.push(json_entry(&entry_name, &value)?);
                included_session_ids.push(session_id);
            }
            Err(message) => failures.push(CollectionFailure {
                collection: "sessions".into(),
                item: session_ids.get(index).cloned(),
                message: redact_text(&message, &credentials, home.as_deref()).0,
            }),
        }
    }

    let log_dir = paths::log_dir();
    let log_result = tauri::async_runtime::spawn_blocking({
        let credentials = credentials.clone();
        let home = home.clone();
        move || collect_logs(&log_dir, &credentials, home.as_deref())
    })
    .await
    .map_err(|e| format!("log collector task failed: {e}"))?;
    let log_manifest = log_result.items;
    entries.extend(log_result.entries);
    failures.extend(log_result.failures);

    let outcome = if failures.is_empty() {
        "complete"
    } else {
        "partial"
    };
    let manifest = json!({
        "formatVersion": 1,
        "generatedAt": generated_at.to_rfc3339(),
        "outcome": outcome,
        "requestedSessionIds": session_ids,
        "includedSessionIds": included_session_ids,
        "failures": failures,
        "excluded": [
            "memory database",
            "persona assets",
            "raw settings",
            "full environment",
            "unselected sessions"
        ],
        "logLimits": {
            "maxBytesPerFile": MAX_LOG_FILE_BYTES,
            "maxBytesTotal": MAX_LOG_TOTAL_BYTES
        },
        "logs": log_manifest,
    });
    entries.push(json_entry("manifest.json", &manifest)?);

    let output_dir = app
        .path()
        .download_dir()
        .map_err(|e| format!("cannot locate Downloads directory: {e}"))?;
    let base_name = format!(
        "agent-friend-diagnostics-{}",
        generated_at.format("%Y%m%d-%H%M%S")
    );
    let output_path = tauri::async_runtime::spawn_blocking(move || {
        write_archive_atomically(&output_dir, &base_name, entries)
    })
    .await
    .map_err(|e| format!("archive writer task failed: {e}"))??;

    Ok(GenerateDiagnosticBundleResult {
        path: output_path.to_string_lossy().to_string(),
        outcome,
        included_session_ids,
        failures,
    })
}

fn load_package_build_metadata(resource_dir: Option<&Path>) -> Value {
    let Some(path) = resource_dir.map(|directory| directory.join(PACKAGE_BUILD_METADATA)) else {
        return Value::Null;
    };
    let Ok(bytes) = fs::read(path) else {
        return Value::Null;
    };
    serde_json::from_slice(&bytes).unwrap_or(Value::Null)
}

fn normalize_description(description: Option<String>) -> Result<Option<String>, String> {
    let description = description.map(|value| value.trim().to_string());
    let description = description.filter(|value| !value.is_empty());
    if description
        .as_ref()
        .is_some_and(|value| value.chars().count() > MAX_DESCRIPTION_CHARS)
    {
        return Err(format!(
            "problem description exceeds {MAX_DESCRIPTION_CHARS} characters"
        ));
    }
    Ok(description)
}

fn normalize_session_ids(session_ids: Vec<String>) -> Result<Vec<String>, String> {
    let mut result = Vec::new();
    for session_id in session_ids {
        let session_id = session_id.trim().to_string();
        if session_id.is_empty() || result.contains(&session_id) {
            continue;
        }
        result.push(session_id);
    }
    if result.len() > MAX_SESSIONS {
        return Err(format!("at most {MAX_SESSIONS} sessions can be exported"));
    }
    Ok(result)
}

fn json_entry(name: &str, value: &Value) -> Result<ArchiveEntry, String> {
    Ok(ArchiveEntry {
        name: name.into(),
        bytes: serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?,
    })
}

struct LogCollectionResult {
    entries: Vec<ArchiveEntry>,
    failures: Vec<CollectionFailure>,
    items: Vec<LogManifestItem>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct LogManifestItem {
    source: String,
    entry: String,
    modified_at_unix_seconds: Option<u64>,
    original_bytes: u64,
    tail_start_offset: u64,
    included_bytes: usize,
}

fn collect_logs(
    log_dir: &Path,
    credentials: &[String],
    home: Option<&Path>,
) -> LogCollectionResult {
    let mut result = LogCollectionResult {
        entries: Vec::new(),
        failures: Vec::new(),
        items: Vec::new(),
    };
    let Ok(canonical_dir) = log_dir.canonicalize() else {
        result.failures.push(CollectionFailure {
            collection: "logs".into(),
            item: None,
            message: "log directory is unavailable".into(),
        });
        return result;
    };
    let Ok(read_dir) = fs::read_dir(&canonical_dir) else {
        result.failures.push(CollectionFailure {
            collection: "logs".into(),
            item: None,
            message: "log directory cannot be read".into(),
        });
        return result;
    };
    let mut candidates = read_dir
        .flatten()
        .filter_map(|entry| {
            let file_type = entry.file_type().ok()?;
            if !file_type.is_file() || file_type.is_symlink() {
                return None;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if !is_allowed_log_name(&name) {
                return None;
            }
            let path = entry.path().canonicalize().ok()?;
            if !path.starts_with(&canonical_dir) {
                return None;
            }
            let modified = entry.metadata().ok()?.modified().ok()?;
            Some((modified, name, path))
        })
        .collect::<Vec<_>>();
    candidates.sort_by(|a, b| b.0.cmp(&a.0));

    let mut remaining = MAX_LOG_TOTAL_BYTES;
    for (modified, name, path) in candidates {
        if remaining == 0 {
            break;
        }
        let budget = MAX_LOG_FILE_BYTES.min(remaining);
        match read_file_tail(&path, budget) {
            Ok((bytes, tail_start_offset, original_bytes)) => {
                remaining = remaining.saturating_sub(bytes.len() as u64);
                let text = String::from_utf8_lossy(&bytes);
                let (text, _) = redact_text(&text, credentials, home);
                let entry_name = format!("logs/{}", sanitize_entry_name(&name));
                result.items.push(LogManifestItem {
                    source: name,
                    entry: entry_name.clone(),
                    modified_at_unix_seconds: modified
                        .duration_since(UNIX_EPOCH)
                        .ok()
                        .map(|duration| duration.as_secs()),
                    original_bytes,
                    tail_start_offset,
                    included_bytes: text.len(),
                });
                result.entries.push(ArchiveEntry {
                    name: entry_name,
                    bytes: text.into_bytes(),
                });
            }
            Err(error) => result.failures.push(CollectionFailure {
                collection: "logs".into(),
                item: Some(name),
                message: error,
            }),
        }
    }
    if result.entries.is_empty() && result.failures.is_empty() {
        result.failures.push(CollectionFailure {
            collection: "logs".into(),
            item: None,
            message: "no supported log files were found".into(),
        });
    }
    result
}

fn is_allowed_log_name(name: &str) -> bool {
    [
        "tauri.log",
        "agent_bridge.log",
        "voice_bridge.log",
        "memory.log",
        "runtime-bridge.log",
        "runtime-voice.log",
        "dev-bridge.log",
        "dev-voice.log",
    ]
    .iter()
    .any(|prefix| name == *prefix || name.starts_with(&format!("{prefix}.")))
}

fn read_file_tail(path: &Path, budget: u64) -> Result<(Vec<u8>, u64, u64), String> {
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let len = file.metadata().map_err(|e| e.to_string())?.len();
    let start = len.saturating_sub(budget);
    file.seek(SeekFrom::Start(start))
        .map_err(|e| e.to_string())?;
    let mut bytes = Vec::with_capacity((len - start) as usize);
    file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok((bytes, start, len))
}

fn sanitize_entry_name(name: &str) -> String {
    name.chars()
        .map(|character| match character {
            'a'..='z' | 'A'..='Z' | '0'..='9' | '.' | '-' | '_' => character,
            _ => '_',
        })
        .collect()
}

fn redact_json(value: &mut Value, credentials: &[String], home: Option<&Path>, count: &mut u64) {
    match value {
        Value::Object(object) => {
            for (key, value) in object.iter_mut() {
                if is_sensitive_key(key) {
                    if !value.is_null() {
                        *value = Value::String("<REDACTED:CREDENTIAL>".into());
                        *count += 1;
                    }
                } else {
                    redact_json(value, credentials, home, count);
                }
            }
        }
        Value::Array(values) => {
            for value in values {
                redact_json(value, credentials, home, count);
            }
        }
        Value::String(text) => {
            let (redacted, replacements) = redact_text(text, credentials, home);
            *text = redacted;
            *count += replacements;
        }
        _ => {}
    }
}

fn is_sensitive_key(key: &str) -> bool {
    let compact = key
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect::<String>();
    compact == "authorization"
        || compact == "cookie"
        || compact == "token"
        || compact == "password"
        || compact == "secret"
        || compact.contains("apikey")
        || compact.ends_with("token")
        || compact.contains("credential")
        || compact.ends_with("clientsecret")
        || compact.ends_with("secretkey")
}

fn redact_text(text: &str, credentials: &[String], home: Option<&Path>) -> (String, u64) {
    let mut result = text.to_string();
    let mut count = 0;
    for credential in credentials {
        let occurrences = result.matches(credential).count() as u64;
        if occurrences > 0 {
            result = result.replace(credential, "<REDACTED:CREDENTIAL>");
            count += occurrences;
        }
    }
    if let Some(home) = home.and_then(Path::to_str) {
        result = result.replace(home, "<USER_HOME>");
        #[cfg(target_os = "windows")]
        {
            result = result.replace(&home.replace('\\', "/"), "<USER_HOME>");
        }
    }
    let mut lines = Vec::new();
    for line in result.lines() {
        let lower = line.to_ascii_lowercase();
        if [
            "authorization:",
            "cookie:",
            "api_key=",
            "apikey=",
            "token=",
            "password=",
            "access_token=",
            "accesstoken=",
            "refresh_token=",
            "refreshtoken=",
            "client_secret=",
            "clientsecret=",
            "secret_key=",
            "secretkey=",
        ]
        .iter()
        .any(|marker| lower.contains(marker))
        {
            lines.push("<REDACTED:CREDENTIAL-LINE>".to_string());
            count += 1;
        } else {
            lines.push(line.to_string());
        }
    }
    (lines.join("\n"), count)
}

fn write_archive_atomically(
    output_dir: &Path,
    base_name: &str,
    entries: Vec<ArchiveEntry>,
) -> Result<PathBuf, String> {
    fs::create_dir_all(output_dir).map_err(|e| format!("create output directory failed: {e}"))?;
    let mut suffix = 0_u32;
    let final_path = loop {
        let file_name = if suffix == 0 {
            format!("{base_name}.zip")
        } else {
            format!("{base_name}-{suffix}.zip")
        };
        let candidate = output_dir.join(file_name);
        let part = candidate.with_extension("zip.part");
        if !candidate.exists() && !part.exists() {
            break candidate;
        }
        suffix = suffix.saturating_add(1);
    };
    let part_path = final_path.with_extension("zip.part");
    let write_result = (|| -> Result<(), String> {
        let file = File::create(&part_path).map_err(|e| format!("create ZIP failed: {e}"))?;
        let mut writer = ZipWriter::new(file);
        let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
        for entry in entries {
            writer
                .start_file(entry.name, options)
                .map_err(|e| format!("start ZIP entry failed: {e}"))?;
            writer
                .write_all(&entry.bytes)
                .map_err(|e| format!("write ZIP entry failed: {e}"))?;
        }
        let file = writer
            .finish()
            .map_err(|e| format!("finish ZIP failed: {e}"))?;
        file.sync_all()
            .map_err(|e| format!("sync ZIP failed: {e}"))?;
        fs::rename(&part_path, &final_path).map_err(|e| format!("publish ZIP failed: {e}"))?;
        Ok(())
    })();
    if let Err(error) = write_result {
        let _ = fs::remove_file(&part_path);
        return Err(error);
    }
    Ok(final_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_and_limits_session_ids() {
        assert_eq!(
            normalize_session_ids(vec![" a ".into(), "a".into(), "b".into()]).unwrap(),
            vec!["a", "b"]
        );
        assert!(
            normalize_session_ids((0..=MAX_SESSIONS).map(|i| i.to_string()).collect()).is_err()
        );
    }

    #[test]
    fn package_build_metadata_is_optional_and_loaded_when_present() {
        let dir = std::env::temp_dir().join(format!(
            "agent-friend-package-metadata-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&dir).unwrap();
        assert!(load_package_build_metadata(Some(&dir)).is_null());
        fs::write(
            dir.join(PACKAGE_BUILD_METADATA),
            br#"{"buildMode":"local-test","distribution":"trusted-beta"}"#,
        )
        .unwrap();

        let metadata = load_package_build_metadata(Some(&dir));

        assert_eq!(metadata["buildMode"], "local-test");
        assert_eq!(metadata["distribution"], "trusted-beta");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn credential_redaction_preserves_ordinary_private_text() {
        let mut value = json!({
            "content": "my home address is private; token-value",
            "authorization": "Bearer token-value",
            "nested": {
                "api_key": "another",
                "apiKey": "camel-api",
                "accessToken": "camel-access",
                "refresh-token": "kebab-refresh",
                "clientSecret": "camel-client",
                "imCredential": "im-secret",
                "cookie": "session=secret"
            }
        });
        let mut count = 0;
        redact_json(&mut value, &["token-value".into()], None, &mut count);
        assert_eq!(
            value["content"],
            "my home address is private; <REDACTED:CREDENTIAL>"
        );
        assert_eq!(value["authorization"], "<REDACTED:CREDENTIAL>");
        assert_eq!(value["nested"]["api_key"], "<REDACTED:CREDENTIAL>");
        for key in [
            "apiKey",
            "accessToken",
            "refresh-token",
            "clientSecret",
            "imCredential",
            "cookie",
        ] {
            assert_eq!(value["nested"][key], "<REDACTED:CREDENTIAL>");
        }
        assert!(count >= 9);
    }

    #[test]
    fn text_redaction_handles_snake_case_and_camel_case_assignments() {
        let input = [
            "safe line",
            "api_key=snake-secret",
            "apiKey=camel-secret",
            "accessToken=access-secret",
            "clientSecret=client-secret",
            "Authorization: Bearer auth-secret",
            "Cookie: session=cookie-secret",
        ]
        .join("\n");
        let (redacted, count) = redact_text(&input, &[], None);
        assert!(redacted.contains("safe line"));
        for secret in [
            "snake-secret",
            "camel-secret",
            "access-secret",
            "client-secret",
            "auth-secret",
            "cookie-secret",
        ] {
            assert!(!redacted.contains(secret));
        }
        assert_eq!(count, 6);
    }

    #[test]
    fn log_name_allowlist_is_narrow() {
        assert!(is_allowed_log_name("agent_bridge.log.1"));
        assert!(is_allowed_log_name("voice_bridge.log"));
        assert!(is_allowed_log_name("runtime-voice.log.1"));
        assert!(is_allowed_log_name("tauri.log.2026-07-15"));
        assert!(!is_allowed_log_name("settings.json"));
        assert!(!is_allowed_log_name("private-agent_bridge.log"));
    }

    #[test]
    fn file_tail_reads_only_the_requested_budget() {
        let path =
            std::env::temp_dir().join(format!("agent-friend-log-tail-{}", rand::random::<u64>()));
        fs::write(&path, b"0123456789").unwrap();
        assert_eq!(read_file_tail(&path, 4).unwrap(), (b"6789".to_vec(), 6, 10));
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn archive_uses_safe_fixed_entry_names() {
        let dir = std::env::temp_dir().join(format!(
            "agent-friend-diagnostics-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&dir).unwrap();
        let path = write_archive_atomically(
            &dir,
            "test",
            vec![ArchiveEntry {
                name: "sessions/session-001.json".into(),
                bytes: b"{}".to_vec(),
            }],
        )
        .unwrap();
        assert!(path.exists());
        assert!(!path.with_extension("zip.part").exists());
        fs::remove_file(path).unwrap();
        fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn final_archive_contains_no_plaintext_credentials() {
        let dir = std::env::temp_dir().join(format!(
            "agent-friend-redacted-archive-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&dir).unwrap();
        let mut session = json!({
            "events": [{
                "payload": {
                    "content": "ordinary private conversation",
                    "apiKey": "managed-secret",
                    "accessToken": "other-secret"
                }
            }]
        });
        let mut count = 0;
        redact_json(&mut session, &["managed-secret".into()], None, &mut count);
        let path = write_archive_atomically(
            &dir,
            "redacted",
            vec![json_entry("sessions/session-001.json", &session).unwrap()],
        )
        .unwrap();

        let file = File::open(&path).unwrap();
        let mut archive = zip::ZipArchive::new(file).unwrap();
        let mut contents = String::new();
        archive
            .by_name("sessions/session-001.json")
            .unwrap()
            .read_to_string(&mut contents)
            .unwrap();
        assert!(contents.contains("ordinary private conversation"));
        assert!(!contents.contains("managed-secret"));
        assert!(!contents.contains("other-secret"));
        assert!(contents.contains("<REDACTED:CREDENTIAL>"));

        fs::remove_file(path).unwrap();
        fs::remove_dir(dir).unwrap();
    }
}
