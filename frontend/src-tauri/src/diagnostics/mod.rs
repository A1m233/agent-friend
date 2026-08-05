use crate::{paths, runtime_config, runtime_services, shell_diagnostics, webview_diagnostics};
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager, State};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipWriter};

mod contract;

const MAX_DESCRIPTION_CHARS: usize = 20_000;
const MAX_SESSIONS: usize = 20;
const MAX_LOG_FILE_BYTES: u64 = 5 * 1024 * 1024;
const MAX_LOG_TOTAL_BYTES: u64 = 20 * 1024 * 1024;
const PACKAGE_BUILD_METADATA: &str = "package-build.json";
const MAX_ATTACHMENT_ORIGINAL_TOTAL_BYTES: u64 = 200 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerateDiagnosticBundleRequest {
    description: Option<String>,
    session_ids: Vec<String>,
    locale: Option<String>,
    timezone: Option<String>,
    #[serde(default)]
    include_attachment_originals: bool,
    #[serde(default)]
    include_render_evidence: bool,
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
    source: ArchiveEntrySource,
}

enum ArchiveEntrySource {
    Bytes(Vec<u8>),
    File(PathBuf),
}

#[derive(Clone)]
struct AttachmentCandidate {
    asset_id: String,
    display_name: String,
    kind: String,
    media_type: String,
    size_bytes: u64,
    sha256: String,
    references: Vec<Value>,
}

#[tauri::command]
pub async fn generate_diagnostic_bundle(
    app: AppHandle,
    request: GenerateDiagnosticBundleRequest,
    services: State<'_, Arc<runtime_services::RuntimeServiceSupervisor>>,
    shell_diagnostics_manager: State<'_, Arc<shell_diagnostics::ShellDiagnosticsManager>>,
    webview_diagnostics_manager: State<'_, Arc<webview_diagnostics::WebViewDiagnosticsManager>>,
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
    let model_catalog_result = {
        let client = client.clone();
        async move {
            let mut url =
                reqwest::Url::parse(runtime_services::RuntimeServiceKind::Bridge.base_url())
                    .map_err(|e| e.to_string())?;
            url.path_segments_mut()
                .map_err(|_| "bridge base URL cannot accept path segments".to_string())?
                .extend(["v1", "models", "catalog"]);
            let response = client.get(url).send().await.map_err(|e| e.to_string())?;
            if !response.status().is_success() {
                return Err(format!("bridge returned HTTP {}", response.status()));
            }
            response.json::<Value>().await.map_err(|e| e.to_string())
        }
    }
    .await;
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
    let mut attachment_candidates = BTreeMap::new();
    if let Some(description) = description {
        let (description, _) = redact_text(&description, &credentials, home.as_deref());
        entries.push(ArchiveEntry {
            name: "problem.txt".into(),
            source: ArchiveEntrySource::Bytes(description.into_bytes()),
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
    let webview2_diagnostics = serde_json::to_value(webview_diagnostics_manager.snapshot())
        .map_err(|error| format!("serialize WebView2 diagnostics failed: {error}"))?;
    let mut render_evidence_export = webview_diagnostics_manager
        .export_render_evidence_for_bundle(request.include_render_evidence);
    let render_evidence_metadata = render_evidence_export.metadata;

    for (name, mut value) in [
        ("diagnostics/application.json", app_info),
        ("diagnostics/environment.json", environment),
        ("diagnostics/runtime-services.json", runtime_services),
        ("diagnostics/runtime-config.json", runtime_config),
        ("diagnostics/webview2.json", webview2_diagnostics),
        (
            "diagnostics/webview2-render-evidence.json",
            render_evidence_metadata,
        ),
    ] {
        let mut count = 0;
        redact_json(&mut value, &credentials, home.as_deref(), &mut count);
        entries.push(json_entry(name, &value)?);
    }
    for artifact in render_evidence_export.artifacts.drain(..) {
        entries.push(ArchiveEntry {
            name: artifact.entry_name,
            source: ArchiveEntrySource::Bytes(artifact.bytes),
        });
    }
    failures.extend(
        render_evidence_export
            .failures
            .drain(..)
            .map(|message| CollectionFailure {
                collection: "webview2-render-evidence".into(),
                item: None,
                message: redact_text(&message, &credentials, home.as_deref()).0,
            }),
    );

    match model_catalog_result {
        Ok(mut value) => {
            let mut count = 0;
            redact_json(&mut value, &credentials, home.as_deref(), &mut count);
            entries.push(json_entry("diagnostics/model-catalog.json", &value)?);
        }
        Err(message) => failures.push(CollectionFailure {
            collection: "model-catalog".into(),
            item: None,
            message: redact_text(&message, &credentials, home.as_deref()).0,
        }),
    }

    for (index, result) in session_results.into_iter().enumerate() {
        match result {
            Ok((session_id, mut value)) => {
                collect_attachment_candidates(&session_id, &value, &mut attachment_candidates);
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

    let attachment_collection = collect_attachment_diagnostics(
        &paths::user_data_dir().join("attachments"),
        attachment_candidates,
        request.include_attachment_originals,
    );
    entries.extend(attachment_collection.entries);
    failures.extend(attachment_collection.failures);
    entries.push(json_entry(
        "diagnostics/attachments.json",
        &attachment_collection.manifest,
    )?);

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

    let shell_export = shell_diagnostics_manager.export_for_bundle().await;
    let shell_artifact_names = shell_export
        .artifacts
        .iter()
        .map(|artifact| artifact.name.clone())
        .collect::<Vec<_>>();
    for artifact in shell_export.artifacts {
        entries.push(ArchiveEntry {
            name: artifact.name,
            source: ArchiveEntrySource::Bytes(artifact.bytes),
        });
    }
    let shell_failure_count = shell_export.failures.len();
    failures.extend(
        shell_export
            .failures
            .into_iter()
            .map(|message| CollectionFailure {
                collection: "windows-shell".into(),
                item: None,
                message,
            }),
    );
    let shell_summary = json!({
        "supported": shell_export.supported,
        "artifacts": shell_artifact_names,
        "failureCount": shell_failure_count,
    });

    let outcome = if failures.is_empty() {
        "complete"
    } else {
        "partial"
    };
    let bundle_contract = contract::BundleContract::load()?;
    let generated_at_rfc3339 = generated_at.to_rfc3339();
    let manifest = build_bundle_manifest(
        &bundle_contract,
        BundleManifestInput {
            generated_at: &generated_at_rfc3339,
            outcome,
            requested_session_ids: &session_ids,
            included_session_ids: &included_session_ids,
            failures: &failures,
            logs: &log_manifest,
            windows_shell: &shell_summary,
            attachments: &attachment_collection.summary,
            render_evidence: &render_evidence_export.summary,
        },
    );
    entries.push(json_entry("manifest.json", &manifest)?);
    bundle_contract.validate_bundle(&manifest, entries.iter().map(|entry| entry.name.as_str()))?;

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

struct AttachmentCollectionResult {
    entries: Vec<ArchiveEntry>,
    failures: Vec<CollectionFailure>,
    manifest: Value,
    summary: Value,
}

fn collect_attachment_candidates(
    session_id: &str,
    session: &Value,
    output: &mut BTreeMap<String, AttachmentCandidate>,
) {
    let Some(events) = session.get("events").and_then(Value::as_array) else {
        return;
    };
    for event in events {
        if event.get("type").and_then(Value::as_str) != Some("user_message") {
            continue;
        }
        let Some(attachments) = event
            .get("payload")
            .and_then(|value| value.get("attachments"))
            .and_then(Value::as_array)
        else {
            continue;
        };
        for attachment in attachments {
            let Some(asset_id) = attachment.get("asset_id").and_then(Value::as_str) else {
                continue;
            };
            let reference = json!({
                "sessionId": session_id,
                "eventId": event.get("uuid").and_then(Value::as_str),
                "model": event.get("meta").and_then(|value| value.get("model")),
            });
            output
                .entry(asset_id.to_string())
                .and_modify(|candidate| candidate.references.push(reference.clone()))
                .or_insert_with(|| AttachmentCandidate {
                    asset_id: asset_id.to_string(),
                    display_name: attachment
                        .get("display_name")
                        .and_then(Value::as_str)
                        .unwrap_or("attachment")
                        .to_string(),
                    kind: attachment
                        .get("kind")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown")
                        .to_string(),
                    media_type: attachment
                        .get("media_type")
                        .and_then(Value::as_str)
                        .unwrap_or("application/octet-stream")
                        .to_string(),
                    size_bytes: attachment
                        .get("size_bytes")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    sha256: attachment
                        .get("sha256")
                        .and_then(Value::as_str)
                        .unwrap_or(asset_id)
                        .to_string(),
                    references: vec![reference],
                });
        }
    }
}

fn collect_attachment_diagnostics(
    root: &Path,
    candidates: BTreeMap<String, AttachmentCandidate>,
    include_originals: bool,
) -> AttachmentCollectionResult {
    let mut entries = Vec::new();
    let mut failures = Vec::new();
    let mut items = Vec::new();
    let estimated_bytes = candidates.values().map(|item| item.size_bytes).sum::<u64>();
    let mut included_bytes = 0_u64;
    let mut skipped_bytes = 0_u64;
    let mut failed_bytes = 0_u64;

    for (index, candidate) in candidates.into_values().enumerate() {
        let valid_id = candidate.asset_id.len() == 64
            && candidate
                .asset_id
                .chars()
                .all(|value| value.is_ascii_hexdigit());
        let metadata_path = root
            .join("metadata")
            .join(format!("{}.json", candidate.asset_id));
        let blob_path = root
            .join("blobs")
            .join(candidate.asset_id.get(..2).unwrap_or("__"))
            .join(&candidate.asset_id);
        let metadata_exists = valid_id && metadata_path.is_file();
        let metadata_matches = metadata_exists
            && fs::read(&metadata_path)
                .ok()
                .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
                .is_some_and(|value| {
                    value.get("asset_id").and_then(Value::as_str)
                        == Some(candidate.asset_id.as_str())
                        && value.get("sha256").and_then(Value::as_str)
                            == Some(candidate.sha256.as_str())
                        && value.get("size_bytes").and_then(Value::as_u64)
                            == Some(candidate.size_bytes)
                });
        let blob_exists = valid_id && blob_path.is_file();
        let actual_size = blob_path.metadata().ok().map(|value| value.len());
        let size_matches = actual_size == Some(candidate.size_bytes);
        let hash_matches = blob_exists
            && hash_file(&blob_path)
                .is_ok_and(|digest| digest == candidate.sha256 && digest == candidate.asset_id);
        let integrity = metadata_matches && blob_exists && size_matches && hash_matches;

        let (original_status, archive_entry) = if !include_originals {
            ("excluded", None)
        } else if !integrity {
            failed_bytes = failed_bytes.saturating_add(candidate.size_bytes);
            failures.push(CollectionFailure {
                collection: "attachments".into(),
                item: Some(candidate.display_name.clone()),
                message: "attachment original is missing or failed integrity verification".into(),
            });
            ("failed", None)
        } else if included_bytes.saturating_add(candidate.size_bytes)
            > MAX_ATTACHMENT_ORIGINAL_TOTAL_BYTES
        {
            skipped_bytes = skipped_bytes.saturating_add(candidate.size_bytes);
            ("skipped", None)
        } else {
            included_bytes = included_bytes.saturating_add(candidate.size_bytes);
            let name = format!(
                "attachments/{:03}-{}",
                index + 1,
                sanitize_entry_name(&candidate.display_name)
            );
            (
                "included",
                Some(ArchiveEntry {
                    name,
                    source: ArchiveEntrySource::File(blob_path),
                }),
            )
        };
        if let Some(entry) = archive_entry {
            entries.push(entry);
        }
        items.push(json!({
            "assetId": candidate.asset_id,
            "displayName": candidate.display_name,
            "kind": candidate.kind,
            "mediaType": candidate.media_type,
            "sizeBytes": candidate.size_bytes,
            "metadataExists": metadata_exists,
            "metadataMatches": metadata_matches,
            "blobExists": blob_exists,
            "sizeMatches": size_matches,
            "hashMatches": hash_matches,
            "references": candidate.references,
            "processingStatus": "notRecorded",
            "originalStatus": original_status,
        }));
    }
    let summary = json!({
        "includeOriginalsRequested": include_originals,
        "itemCount": items.len(),
        "estimatedBytes": estimated_bytes,
        "includedBytes": included_bytes,
        "skippedBytes": skipped_bytes,
        "failedBytes": failed_bytes,
        "maxOriginalBytes": MAX_ATTACHMENT_ORIGINAL_TOTAL_BYTES,
    });
    AttachmentCollectionResult {
        entries,
        failures,
        manifest: json!({"formatVersion": 1, "summary": summary, "items": items}),
        summary,
    }
}

fn hash_file(path: &Path) -> Result<String, String> {
    let mut file = File::open(path).map_err(|error| error.to_string())?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
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
        source: ArchiveEntrySource::Bytes(
            serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?,
        ),
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

struct BundleManifestInput<'a> {
    generated_at: &'a str,
    outcome: &'a str,
    requested_session_ids: &'a [String],
    included_session_ids: &'a [String],
    failures: &'a [CollectionFailure],
    logs: &'a [LogManifestItem],
    windows_shell: &'a Value,
    attachments: &'a Value,
    render_evidence: &'a Value,
}

fn build_bundle_manifest(
    contract: &contract::BundleContract,
    input: BundleManifestInput<'_>,
) -> Value {
    json!({
        "formatVersion": contract.format_version(),
        "schemaRevision": contract.schema_revision(),
        "capabilities": contract.capability_ids(),
        "generatedAt": input.generated_at,
        "outcome": input.outcome,
        "requestedSessionIds": input.requested_session_ids,
        "includedSessionIds": input.included_session_ids,
        "failures": input.failures,
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
        "logs": input.logs,
        "windowsShell": input.windows_shell,
        "attachments": input.attachments,
        "renderEvidence": input.render_evidence,
    })
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
                    source: ArchiveEntrySource::Bytes(text.into_bytes()),
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
            match entry.source {
                ArchiveEntrySource::Bytes(bytes) => writer
                    .write_all(&bytes)
                    .map_err(|e| format!("write ZIP entry failed: {e}"))?,
                ArchiveEntrySource::File(path) => {
                    let mut input =
                        File::open(path).map_err(|e| format!("open ZIP source failed: {e}"))?;
                    std::io::copy(&mut input, &mut writer)
                        .map_err(|e| format!("copy ZIP source failed: {e}"))?;
                }
            }
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
    fn current_bundle_manifest_matches_machine_contract() {
        let contract = contract::BundleContract::load().expect("load bundle contract");
        let requested_session_ids = Vec::new();
        let included_session_ids = Vec::new();
        let failures = Vec::new();
        let logs = Vec::new();
        let windows_shell = json!({
            "supported": true,
            "artifacts": ["diagnostics/windows-shell/current.json"],
            "failureCount": 0,
        });
        let attachments = json!({"itemCount": 0});
        let render_evidence = json!({
            "supported": true,
            "includeRequested": false,
            "pendingCapture": null,
            "artifacts": [],
            "failureCount": 0,
        });
        let manifest = build_bundle_manifest(
            &contract,
            BundleManifestInput {
                generated_at: "2026-08-01T00:00:00+00:00",
                outcome: "complete",
                requested_session_ids: &requested_session_ids,
                included_session_ids: &included_session_ids,
                failures: &failures,
                logs: &logs,
                windows_shell: &windows_shell,
                attachments: &attachments,
                render_evidence: &render_evidence,
            },
        );

        contract
            .validate_bundle(
                &manifest,
                [
                    "manifest.json",
                    "diagnostics/application.json",
                    "diagnostics/environment.json",
                    "diagnostics/runtime-services.json",
                    "diagnostics/runtime-config.json",
                    "diagnostics/webview2.json",
                    "diagnostics/webview2-render-evidence.json",
                    "diagnostics/attachments.json",
                    "diagnostics/windows-shell/current.json",
                ],
            )
            .expect("current manifest should satisfy bundle contract");
    }

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
                source: ArchiveEntrySource::Bytes(b"{}".to_vec()),
            }],
        )
        .unwrap();
        assert!(path.exists());
        assert!(!path.with_extension("zip.part").exists());
        fs::remove_file(path).unwrap();
        fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn attachment_diagnostics_excludes_originals_by_default() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-attachment-diagnostics-{}",
            rand::random::<u64>()
        ));
        let payload = b"private-image";
        let asset_id = format!("{:x}", Sha256::digest(payload));
        let blob = root.join("blobs").join(&asset_id[..2]).join(&asset_id);
        fs::create_dir_all(blob.parent().unwrap()).unwrap();
        fs::create_dir_all(root.join("metadata")).unwrap();
        fs::write(&blob, payload).unwrap();
        fs::write(
            root.join("metadata").join(format!("{asset_id}.json")),
            serde_json::to_vec(&json!({
                "asset_id": asset_id,
                "sha256": asset_id,
                "size_bytes": payload.len(),
            }))
            .unwrap(),
        )
        .unwrap();
        let candidates = BTreeMap::from([(
            asset_id.clone(),
            AttachmentCandidate {
                asset_id: asset_id.clone(),
                display_name: "private.png".into(),
                kind: "image".into(),
                media_type: "image/png".into(),
                size_bytes: payload.len() as u64,
                sha256: asset_id,
                references: vec![],
            },
        )]);

        let result = collect_attachment_diagnostics(&root, candidates, false);

        assert!(result.entries.is_empty());
        assert_eq!(result.manifest["items"][0]["originalStatus"], "excluded");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn attachment_diagnostics_streams_explicitly_selected_original() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-attachment-original-{}",
            rand::random::<u64>()
        ));
        let payload = b"private-document";
        let asset_id = format!("{:x}", Sha256::digest(payload));
        let blob = root.join("blobs").join(&asset_id[..2]).join(&asset_id);
        fs::create_dir_all(blob.parent().unwrap()).unwrap();
        fs::create_dir_all(root.join("metadata")).unwrap();
        fs::write(&blob, payload).unwrap();
        fs::write(
            root.join("metadata").join(format!("{asset_id}.json")),
            serde_json::to_vec(&json!({
                "asset_id": asset_id,
                "sha256": asset_id,
                "size_bytes": payload.len(),
            }))
            .unwrap(),
        )
        .unwrap();
        let candidates = BTreeMap::from([(
            asset_id.clone(),
            AttachmentCandidate {
                asset_id: asset_id.clone(),
                display_name: "notes.txt".into(),
                kind: "document".into(),
                media_type: "text/plain".into(),
                size_bytes: payload.len() as u64,
                sha256: asset_id,
                references: vec![],
            },
        )]);

        let result = collect_attachment_diagnostics(&root, candidates, true);

        assert_eq!(result.entries.len(), 1);
        assert_eq!(result.manifest["items"][0]["originalStatus"], "included");
        assert_eq!(result.summary["includedBytes"], payload.len() as u64);
        fs::remove_dir_all(root).unwrap();
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
