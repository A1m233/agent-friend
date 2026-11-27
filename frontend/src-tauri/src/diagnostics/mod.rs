use crate::{
    desktop_instance, install_contract, paths, pet_input_evidence, runtime_config,
    runtime_services, shell_diagnostics, webview_diagnostics,
};
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipWriter};

mod contract;
mod installer_attempts;
pub mod job;
mod native_crash_export;
#[cfg(test)]
mod performance;
mod sandbox_incidents;
mod scratch;
mod selection;
mod session_stream;
mod staged_log;

const MAX_DESCRIPTION_CHARS: usize = 20_000;
const PACKAGE_BUILD_METADATA: &str = "package-build.json";

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerateDiagnosticBundleRequest {
    description: Option<String>,
    session_ids: Vec<String>,
    #[serde(default = "default_session_turn_limit")]
    session_turn_limit: Option<u32>,
    locale: Option<String>,
    timezone: Option<String>,
    #[serde(default)]
    include_attachment_originals: bool,
    #[serde(default)]
    include_render_evidence: bool,
    #[serde(default)]
    include_crash_dumps: bool,
    #[serde(default)]
    all_retained_logs: bool,
    #[serde(default)]
    verify_install_files: bool,
}

fn default_session_turn_limit() -> Option<u32> {
    Some(10)
}

#[tauri::command]
pub async fn get_diagnostic_log_estimate() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(estimate_logs)
        .await
        .map_err(|e| e.to_string())?
}

fn estimate_logs() -> Result<Value, String> {
    let mut bytes = 0_u64;
    let mut files = 0_u64;
    for entry in fs::read_dir(paths::log_dir()).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_type().map_err(|e| e.to_string())?.is_file()
            && is_allowed_log_name(&entry.file_name().to_string_lossy())
        {
            bytes = bytes.saturating_add(entry.metadata().map_err(|e| e.to_string())?.len());
            files += 1;
        }
    }
    Ok(json!({"retainedBytes":bytes,"fileCount":files,"estimatedAt":Local::now().to_rfc3339()}))
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CollectionFailure {
    collection: String,
    item: Option<String>,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerateDiagnosticBundleResult {
    path: String,
    outcome: &'static str,
    included_session_ids: Vec<String>,
    failures: Vec<CollectionFailure>,
    warnings: Vec<CollectionFailure>,
    metrics: Value,
}

struct ArchiveEntry {
    name: String,
    source: ArchiveEntrySource,
}

enum ArchiveEntrySource {
    Bytes(Vec<u8>),
    Scratch(scratch::Scratch),
    StagedLog(staged_log::StagedLog),
    NativeCrashDump(crate::native_crash::ExportDump),
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

async fn run_bundle(
    app: AppHandle,
    request: GenerateDiagnosticBundleRequest,
    control: Arc<job::Control>,
) -> Result<GenerateDiagnosticBundleResult, String> {
    let services = app.state::<Arc<runtime_services::RuntimeServiceSupervisor>>();
    let desktop_instance_state = app.state::<desktop_instance::DesktopInstanceState>();
    let shell_diagnostics_manager = app.state::<Arc<shell_diagnostics::ShellDiagnosticsManager>>();
    let webview_diagnostics_manager =
        app.state::<Arc<webview_diagnostics::WebViewDiagnosticsManager>>();
    control.stage("preparing")?;
    scratch::reap(&paths::diagnostic_work_dir())?;
    let output_dir = app
        .path()
        .download_dir()
        .map_err(|e| format!("cannot locate Downloads directory: {e}"))?;
    fs::create_dir_all(&output_dir).map_err(|e| e.to_string())?;
    scratch::ensure_space(&output_dir, 0)?;
    let selection = json!({
        "logs": if request.all_retained_logs { "all-retained" } else { "recent" },
        "sessionTurnLimit": request.session_turn_limit,
        "includeAttachmentOriginals": request.include_attachment_originals,
        "verifyInstallFiles": request.verify_install_files,
    });
    let description = normalize_description(request.description)?;
    let session_ids = normalize_session_ids(request.session_ids)?;
    if request
        .session_turn_limit
        .is_some_and(|value| !(1..=100).contains(&value))
    {
        return Err("sessionTurnLimit must be between 1 and 100, or null".into());
    }
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
    control.stage("basic-state")?;
    let base_url = runtime_services::RuntimeServiceKind::Bridge.base_url();
    let mut basic = control
        .wait(futures_util::future::join_all(
            [
                "v1/models/catalog",
                "v1/execution-environments/diagnostics",
                "v1/command-runtime/diagnostics",
                "v1/desktop-applications/diagnostics",
            ]
            .into_iter()
            .map(|path| fetch_json(&client, &base_url, path)),
        ))
        .await?
        .into_iter();
    let model_catalog_result = basic.next().unwrap();
    let execution_environments_result = basic.next().unwrap();
    let command_runtime_result = basic.next().unwrap();
    let desktop_applications_result = basic.next().unwrap();
    // Session exports may scan a large persisted snapshot. Do not apply the health-probe timeout.
    let session_client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| format!("create session diagnostics client failed: {e}"))?;
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
    if request.verify_install_files {
        control.stage("install-files")?;
    }
    let install_integrity = collect_install_integrity(
        resource_dir.as_deref(),
        services.install_integrity_runtime_evidence().await,
        request.verify_install_files,
        &control,
    );
    control.check()?;
    let desktop_instance = desktop_instance_state.snapshot();
    let execution_environments = match execution_environments_result {
        Ok(value) => value,
        Err(message) => {
            failures.push(CollectionFailure {
                collection: "execution-environments".into(),
                item: None,
                message: redact_text(&message, &credentials, home.as_deref()).0,
            });
            json!({
                "schemaVersion": 1,
                "status": "unavailable",
                "providers": [],
                "errorCode": "bridge_unavailable"
            })
        }
    };

    let mut command_runtime = match command_runtime_result {
        Ok(value) => value,
        Err(message) => {
            failures.push(CollectionFailure {
                collection: "command-runtime".into(),
                item: None,
                message: redact_text(&message, &credentials, home.as_deref()).0,
            });
            json!({"schemaVersion": 1, "status": "unavailable", "events": [],
                   "gaps": ["bridge_unavailable"]})
        }
    };
    let incidents = command_runtime
        .as_object_mut()
        .and_then(|value| value.remove("incidentEvidence"))
        .unwrap_or_else(|| sandbox_incidents::collect(&paths::log_dir()));

    let desktop_applications = desktop_applications_result.unwrap_or_else(|message| {
        failures.push(CollectionFailure {
            collection: "desktop-applications".into(),
            item: None,
            message: redact_text(&message, &credentials, home.as_deref()).0,
        });
        json!({"schemaVersion":1,"status":"unavailable","records":[],"gaps":["bridge_unavailable"]})
    });
    for (name, mut value) in [
        (
            "diagnostics/desktop-applications.json",
            desktop_applications,
        ),
        ("diagnostics/application.json", app_info),
        ("diagnostics/environment.json", environment),
        ("diagnostics/runtime-services.json", runtime_services),
        ("diagnostics/runtime-config.json", runtime_config),
        ("diagnostics/webview2.json", webview2_diagnostics),
        (
            "diagnostics/webview2-render-evidence.json",
            render_evidence_metadata,
        ),
        ("diagnostics/desktop-instance.json", desktop_instance),
        (
            "diagnostics/execution-environments.json",
            execution_environments,
        ),
        ("diagnostics/command-runtime.json", command_runtime),
        ("diagnostics/command-runtime-incidents.json", incidents),
        ("install/integrity.json", install_integrity),
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

    control.stage("sessions")?;
    for (index, session_id) in session_ids.iter().enumerate() {
        let result = session_stream::collect(
            &session_client,
            reqwest::Url::parse(&runtime_services::RuntimeServiceKind::Bridge.base_url())
                .map_err(|e| e.to_string())?,
            session_id,
            request.session_turn_limit,
            &credentials,
            home.as_deref(),
            &control,
            &mut attachment_candidates,
        )
        .await;
        control.check()?;
        match result {
            Ok(export) => {
                if !export.complete {
                    failures.push(CollectionFailure {
                        collection: "session-range".into(),
                        item: Some(session_id.clone()),
                        message: "会话记录存在读取或格式缺口，详见 coverage。".into(),
                    });
                }
                entries.push(ArchiveEntry {
                    name: format!("sessions/session-{:03}.json", index + 1),
                    source: ArchiveEntrySource::Scratch(export.file),
                });
                included_session_ids.push(session_id.clone());
            }
            Err(message) => failures.push(CollectionFailure {
                collection: "sessions".into(),
                item: Some(session_id.clone()),
                message: redact_text(&message, &credentials, home.as_deref()).0,
            }),
        }
    }
    control.stage("attachments")?;
    let attachment_collection = collect_attachment_diagnostics_controlled(
        &paths::user_data_dir().join("attachments"),
        attachment_candidates,
        request.include_attachment_originals,
        &control,
    );
    entries.extend(attachment_collection.entries);
    failures.extend(attachment_collection.failures);
    let mut attachment_manifest = attachment_collection.manifest;
    redact_json(
        &mut attachment_manifest,
        &credentials,
        home.as_deref(),
        &mut 0,
    );
    entries.push(json_entry(
        "diagnostics/attachments.json",
        &attachment_manifest,
    )?);

    let (attempt_entries, attempt_failures) = tauri::async_runtime::spawn_blocking(|| {
        let mut entries = Vec::new();
        let mut failures = Vec::new();
        let profile = install_contract::embedded_profile()?;
        installer_attempts::collect(
            &std::env::temp_dir(),
            &profile.product_name,
            &profile.profile_id,
            cfg!(windows),
            &mut entries,
            &mut failures,
        )?;
        Ok::<_, String>((entries, failures))
    })
    .await
    .map_err(|e| format!("installer log collector task failed: {e}"))??;
    entries.extend(attempt_entries);
    failures.extend(attempt_failures);

    control.stage("logs")?;
    let log_dir = paths::log_dir();
    let log_result = tauri::async_runtime::spawn_blocking({
        let credentials = credentials.clone();
        let home = home.clone();
        let control = control.clone();
        move || {
            collect_logs_selected(
                &log_dir,
                &credentials,
                home.as_deref(),
                request.all_retained_logs,
                &control,
            )
        }
    })
    .await
    .map_err(|e| format!("log collector task failed: {e}"))?;
    let log_manifest = log_result.items;
    entries.extend(log_result.entries);
    failures.extend(log_result.failures);

    control.stage("incident-evidence")?;
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

    // The pet may be unresponsive or gone. This collector reads retained disk facts only,
    // with its own budget, and never waits for a renderer response or a writer flush.
    let mut pet_export = tauri::async_runtime::spawn_blocking(|| {
        pet_input_evidence::export_for_bundle(&paths::pet_input_evidence_dir())
    })
    .await
    .unwrap_or_else(|_| pet_input_evidence::export_failed("read-failed"));
    if let Some(manager) = app.try_state::<pet_input_evidence::PetInputEvidence>() {
        // Atomic-only export-time status exposes failures which never reached the disk.
        pet_input_evidence::attach_current(&mut pet_export, manager.snapshot());
    }
    let pet_summary = append_pet_input_export(pet_export, &mut entries, &mut failures);

    let mut render_export = tauri::async_runtime::spawn_blocking(|| {
        crate::pet_render_evidence::journal::export(&paths::pet_render_evidence_dir())
    })
    .await
    .unwrap_or_else(|_| crate::pet_render_evidence::journal::Export::failed());
    if let Some(manager) = app.try_state::<crate::pet_render_evidence::RenderEvidence>() {
        let health = manager.snapshot();
        if health["writerStopped"] == true || health["writeFailed"].as_u64().unwrap_or(0) > 0 {
            render_export
                .failures
                .push("writer-reported-failure".into());
        }
        render_export.coverage["current"] = health;
    }
    entries.push(json_entry(
        "diagnostics/pet-render/coverage.json",
        &render_export.coverage,
    )?);
    for (name, bytes) in render_export.entries {
        entries.push(ArchiveEntry {
            name,
            source: ArchiveEntrySource::Bytes(bytes),
        });
    }
    for message in render_export.failures {
        failures.push(CollectionFailure {
            collection: "pet-render".into(),
            item: None,
            message,
        });
    }

    let include_crash_dumps = request.include_crash_dumps;
    let crash_export = tauri::async_runtime::spawn_blocking(move || {
        crate::native_crash::export_for_bundle(include_crash_dumps)
    })
    .await;
    let warnings = native_crash_export::append(
        crash_export.map_err(|_| ()),
        include_crash_dumps,
        &mut entries,
        &mut failures,
    )?;

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
            warnings: &warnings,
            logs: &log_manifest,
            windows_shell: &shell_summary,
            attachments: &attachment_collection.summary,
            render_evidence: &render_evidence_export.summary,
            pet_input_evidence: &pet_summary,
            selection: &selection,
        },
    );
    entries.push(json_entry("manifest.json", &manifest)?);
    bundle_contract.validate_bundle(&manifest, entries.iter().map(|entry| entry.name.as_str()))?;

    let base_name = format!(
        "agent-friend-diagnostics-{}",
        generated_at.format("%Y%m%d-%H%M%S")
    );
    control.stage("archive")?;
    let output_path = tauri::async_runtime::spawn_blocking(move || {
        write_archive_controlled(&output_dir, &base_name, entries, &control)
    })
    .await
    .map_err(|e| format!("archive writer task failed: {e}"))??;

    Ok(GenerateDiagnosticBundleResult {
        path: output_path.to_string_lossy().to_string(),
        outcome,
        included_session_ids,
        failures,
        warnings,
        metrics: Value::Null, // Filled by the task owner after publication and cleanup.
    })
}

fn append_pet_input_export(
    export: pet_input_evidence::Export,
    entries: &mut Vec<ArchiveEntry>,
    failures: &mut Vec<CollectionFailure>,
) -> Value {
    for artifact in export.artifacts {
        entries.push(ArchiveEntry {
            name: artifact.name,
            source: ArchiveEntrySource::Bytes(artifact.bytes),
        });
    }
    failures.extend(
        export
            .failures
            .into_iter()
            .map(|message| CollectionFailure {
                collection: "pet-input-evidence".into(),
                item: None,
                message,
            }),
    );
    export.summary
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

fn collect_install_integrity(
    resource_dir: Option<&Path>,
    runtime: Value,
    verify_files: bool,
    control: &job::Control,
) -> Value {
    let embedded = install_contract::embedded_identity();
    let embedded_error_code = embedded
        .as_ref()
        .err()
        .map(|error| stable_install_error(error).to_string());
    let shell = embedded
        .as_ref()
        .ok()
        .and_then(|value| value.as_ref())
        .map(|(identity, identity_sha256)| {
            json!({
                "available": true,
                "buildId": identity.build_id,
                "profileId": identity.profile_id,
                "buildIdentitySha256": identity_sha256,
                "productVersion": identity.product_version,
                "buildMode": identity.build_mode,
                "distribution": identity.distribution,
                "sourceCommit": identity.source_commit,
                "sourceTreeSha256": identity.source_tree_sha256,
                "dirty": identity.dirty,
            })
        })
        .unwrap_or_else(|| json!({"available": false}));

    let receipt_path = resource_dir.map(|root| root.join(install_contract::ACTIVE_INSTALL_FILE));
    let receipt = receipt_path
        .as_deref()
        .filter(|path| path.is_file())
        .and_then(|path| fs::read(path).ok())
        .and_then(|bytes| {
            serde_json::from_slice::<install_contract::ActiveInstallReceipt>(&bytes).ok()
        });
    let receipt_summary = receipt
        .as_ref()
        .map(|receipt| {
            json!({
                "present": true,
                "parseable": true,
                "buildId": receipt.build_id,
                "profileId": receipt.profile_id,
                "buildIdentitySha256": receipt.build_identity_sha256,
                "payloadManifestSha256": receipt.payload_manifest_sha256,
                "payloadRelativePath": receipt.payload_relative_path.replace('\\', "/"),
                "activatedAt": receipt.activated_at,
            })
        })
        .unwrap_or_else(|| {
            json!({
                "present": receipt_path.as_deref().is_some_and(Path::is_file),
                "parseable": false,
            })
        });

    let observed_mismatch = embedded
        .as_ref()
        .ok()
        .and_then(|value| value.as_ref())
        .is_some_and(|(identity, identity_sha256)| {
            runtime
                .get("observedReadiness")
                .and_then(Value::as_object)
                .is_some_and(|observed| {
                    observed.values().any(|value| {
                        value.is_object()
                            && (value.get("buildId").and_then(Value::as_str)
                                != Some(identity.build_id.as_str())
                                || value.get("profileId").and_then(Value::as_str)
                                    != Some(identity.profile_id.as_str())
                                || value.get("buildIdentitySha256").and_then(Value::as_str)
                                    != Some(identity_sha256.as_str()))
                    })
                })
        });

    let active_result = if verify_files {
        install_contract::discover_active_payload_cancellable(resource_dir, &|| control.check())
    } else {
        Ok(None)
    };
    let (mut status, error_code, payload) = match active_result {
        Ok(Some(active)) if observed_mismatch => (
            "runtime_identity_mismatch",
            Some("runtime_identity_mismatch".to_string()),
            json!({
                "validated": true,
                "buildId": active.identity.build_id,
                "profileId": active.identity.profile_id,
                "buildIdentitySha256": active.identity_sha256,
                "payloadRelativePath": active.receipt.payload_relative_path.replace('\\', "/"),
                "payloadManifestSha256": active.receipt.payload_manifest_sha256,
                "runtimeExecutableSha256": active.runtime_executable_sha256,
            }),
        ),
        Ok(Some(active)) => (
            "consistent",
            None,
            json!({
                "validated": true,
                "buildId": active.identity.build_id,
                "profileId": active.identity.profile_id,
                "buildIdentitySha256": active.identity_sha256,
                "payloadRelativePath": active.receipt.payload_relative_path.replace('\\', "/"),
                "payloadManifestSha256": active.receipt.payload_manifest_sha256,
                "runtimeExecutableSha256": active.runtime_executable_sha256,
            }),
        ),
        Ok(None) => (
            "unavailable",
            embedded_error_code,
            json!({"validated": false}),
        ),
        Err(error) => {
            let error_code = stable_install_error(&error).to_string();
            let status = classify_install_integrity_status(&error_code);
            (status, Some(error_code), json!({"validated": false}))
        }
    };

    if !verify_files {
        status = if observed_mismatch {
            "runtime_identity_mismatch"
        } else {
            "not_checked"
        };
    }

    let legacy_layout_present = resource_dir.is_some_and(|root| {
        root.join("runtime")
            .join("python")
            .join("agent-friend-runtime.exe")
            .is_file()
    });
    let incomplete_transaction_count = resource_dir
        .map(|root| root.join(".installer-transactions"))
        .filter(|path| path.is_dir())
        .and_then(|path| fs::read_dir(path).ok())
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .filter(|entry| entry.path().is_dir())
                .count()
        })
        .unwrap_or(0);
    if status == "consistent" && incomplete_transaction_count > 0 {
        status = "incomplete_install";
    }

    json!({
        "schemaVersion": 2,
        "fileVerificationRequested": verify_files,
        "status": status,
        "errorCode": error_code,
        "shell": shell,
        "activeInstall": receipt_summary,
        "payload": payload,
        "runtime": runtime,
        "residue": {
            "legacyFixedRuntimePresent": legacy_layout_present,
            "incompleteTransactionCount": incomplete_transaction_count,
        },
    })
}

fn stable_install_error(error: &str) -> &str {
    error
        .split(':')
        .next()
        .unwrap_or("install_integrity_unknown")
}

fn classify_install_integrity_status(error_code: &str) -> &'static str {
    match error_code {
        "install_integrity_shell_receipt_mismatch" => "shell_receipt_mismatch",
        "runtime_identity_mismatch" => "runtime_identity_mismatch",
        code if code.contains("hash_mismatch")
            || code.contains("identity_mismatch")
            || code.contains("required_capability_missing") =>
        {
            "payload_hash_mismatch"
        }
        code if code.contains("unreadable")
            || code.contains("invalid")
            || code.contains("unsafe_path")
            || code.contains("escaped_root")
            || code.contains("duplicate_path") =>
        {
            "incomplete_install"
        }
        _ => "unavailable",
    }
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

#[cfg(test)]
fn collect_attachment_diagnostics(
    root: &Path,
    candidates: BTreeMap<String, AttachmentCandidate>,
    include_originals: bool,
) -> AttachmentCollectionResult {
    collect_attachment_diagnostics_controlled(
        root,
        candidates,
        include_originals,
        &job::Control::detached(),
    )
}

fn collect_attachment_diagnostics_controlled(
    root: &Path,
    candidates: BTreeMap<String, AttachmentCandidate>,
    include_originals: bool,
    control: &job::Control,
) -> AttachmentCollectionResult {
    let mut entries = Vec::new();
    let mut failures = Vec::new();
    let mut items = Vec::new();
    let estimated_bytes = candidates.values().map(|item| item.size_bytes).sum::<u64>();
    let mut included_bytes = 0_u64;
    let skipped_bytes = 0_u64;
    let mut failed_bytes = 0_u64;

    for (index, candidate) in candidates.into_values().enumerate() {
        if control.check().is_err() {
            break;
        }
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
        let actual_size = if valid_id {
            blob_path.metadata().ok().map(|value| value.len())
        } else {
            None
        };
        let size_matches = actual_size == Some(candidate.size_bytes);
        let (staged, hash_matches, hash_check) = if !include_originals {
            (None, None, "not_requested")
        } else if metadata_matches && blob_exists && size_matches {
            match stage_attachment(
                &blob_path,
                candidate.size_bytes,
                &candidate.sha256,
                &candidate.asset_id,
                control,
            ) {
                Ok(file) => (Some(file), Some(true), "verified"),
                Err(message) if message == "attachment integrity mismatch" => {
                    (None, Some(false), "mismatch")
                }
                Err(_) => (None, None, "unavailable"),
            }
        } else {
            (None, None, "unavailable")
        };
        let (original_status, archive_entry) = if !include_originals {
            ("excluded", None)
        } else if let Some(file) = staged {
            included_bytes = included_bytes.saturating_add(candidate.size_bytes);
            (
                "included",
                Some(ArchiveEntry {
                    name: format!(
                        "attachments/{:03}-{}",
                        index + 1,
                        sanitize_entry_name(&candidate.display_name)
                    ),
                    source: ArchiveEntrySource::Scratch(file),
                }),
            )
        } else {
            failed_bytes = failed_bytes.saturating_add(candidate.size_bytes);
            failures.push(CollectionFailure {
                collection: "attachments".into(),
                item: Some(candidate.display_name.clone()),
                message: "attachment original is missing or failed integrity verification".into(),
            });
            ("failed", None)
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
            "hashCheck": hash_check,
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
        "maxOriginalBytes": null,
    });
    AttachmentCollectionResult {
        entries,
        failures,
        manifest: json!({"formatVersion": 2, "summary": summary, "items": items}),
        summary,
    }
}

fn stage_attachment(
    path: &Path,
    expected_size: u64,
    expected_hash: &str,
    asset_id: &str,
    control: &job::Control,
) -> Result<scratch::Scratch, String> {
    let mut input = File::open(path).map_err(|e| e.to_string())?;
    let mut file = scratch::Scratch::new()?.track_cleanup(control);
    scratch::ensure_space(file.path(), expected_size)?;
    let mut digest = Sha256::new();
    let mut size = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        control.check()?;
        let count = input.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        size = size.saturating_add(count as u64);
        if size > expected_size {
            return Err("attachment changed during collection".into());
        }
        digest.update(&buffer[..count]);
        file.file
            .write_all(&buffer[..count])
            .map_err(|e| e.to_string())?;
        control.advance(count as u64)?;
    }
    let actual = format!("{:x}", digest.finalize());
    if size != expected_size || actual != expected_hash || actual != asset_id {
        return Err("attachment integrity mismatch".into());
    }
    file.rewind()?;
    Ok(file)
}

async fn fetch_json(client: &reqwest::Client, base: &str, path: &str) -> Result<Value, String> {
    let url = reqwest::Url::parse(base)
        .and_then(|url| url.join(path))
        .map_err(|e| e.to_string())?;
    let response = client.get(url).send().await.map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("bridge returned HTTP {}", response.status()));
    }
    response.json().await.map_err(|e| e.to_string())
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
    read_bytes: u64,
    omission: Option<&'static str>,
}

struct BundleManifestInput<'a> {
    generated_at: &'a str,
    outcome: &'a str,
    requested_session_ids: &'a [String],
    included_session_ids: &'a [String],
    failures: &'a [CollectionFailure],
    warnings: &'a [CollectionFailure],
    logs: &'a [LogManifestItem],
    windows_shell: &'a Value,
    attachments: &'a Value,
    render_evidence: &'a Value,
    pet_input_evidence: &'a Value,
    selection: &'a Value,
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
        "warnings": input.warnings,
        "excluded": [
            "memory database",
            "persona assets",
            "raw settings",
            "full environment",
            "unselected sessions"
        ],
        "selection": input.selection,
        "exportMetrics": null,
        "logLimits": {
            "budgetUnit": "sourceBytes",
            "maxBytesPerFile": if input.selection["logs"] == "all-retained" { None } else { Some(selection::FAMILY_BYTES) },
            "maxBytesPerFamily": if input.selection["logs"] == "all-retained" { None } else { Some(selection::FAMILY_BYTES) },
            "maxBytesTotal": if input.selection["logs"] == "all-retained" { None } else { Some(selection::TOTAL_BYTES) }
        },
        "logs": input.logs,
        "windowsShell": input.windows_shell,
        "attachments": input.attachments,
        "renderEvidence": input.render_evidence,
        "petInputEvidence": input.pet_input_evidence,
    })
}

#[cfg(test)]
fn collect_logs(
    log_dir: &Path,
    credentials: &[String],
    home: Option<&Path>,
) -> LogCollectionResult {
    collect_logs_selected(log_dir, credentials, home, true, &job::Control::detached())
}

fn collect_logs_selected(
    log_dir: &Path,
    credentials: &[String],
    home: Option<&Path>,
    all: bool,
    control: &job::Control,
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
        .take_while(|_| control.check().is_ok())
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
            let metadata = entry.metadata().ok()?;
            let modified = metadata.modified().ok()?;
            Some((
                modified,
                name,
                path,
                metadata.created().ok(),
                metadata.len(),
            ))
        })
        .collect::<Vec<_>>();
    candidates.sort_by(|a, b| b.0.cmp(&a.0));

    let mut budget = selection::Budget::new(all);
    for (modified, name, path, created, observed_size) in candidates {
        if control.check().is_err() {
            break;
        }
        let input = match File::open(&path) {
            Ok(input) => input,
            Err(error) => {
                result.failures.push(CollectionFailure {
                    collection: "logs".into(),
                    item: Some(name),
                    message: error.to_string(),
                });
                continue;
            }
        };
        let metadata = match input.metadata() {
            Ok(metadata) => metadata,
            Err(error) => {
                result.failures.push(CollectionFailure {
                    collection: "logs".into(),
                    item: Some(name),
                    message: error.to_string(),
                });
                continue;
            }
        };
        if metadata.created().ok() != created || metadata.len() < observed_size {
            result.failures.push(CollectionFailure {
                collection: "logs".into(),
                item: Some(name),
                message: "log rotated or was truncated while selecting the snapshot".into(),
            });
            continue;
        }
        let original = metadata.len();
        let allowed = budget.allocate(selection::family(&name).unwrap_or(&name), original);
        match staged_log::StagedLog::collect_open(
            input,
            credentials,
            home,
            if all { None } else { Some(allowed) },
            control,
        ) {
            Ok(log) => {
                let entry_name = format!("logs/{}", sanitize_entry_name(&name));
                result.items.push(LogManifestItem {
                    source: name,
                    entry: entry_name.clone(),
                    modified_at_unix_seconds: modified
                        .duration_since(UNIX_EPOCH)
                        .ok()
                        .map(|duration| duration.as_secs()),
                    original_bytes: log.original_bytes,
                    tail_start_offset: log.tail_start_offset,
                    included_bytes: log.included_bytes,
                    read_bytes: log.read_bytes,
                    omission: (log.tail_start_offset > 0).then_some("recent_log_budget"),
                });
                result.entries.push(ArchiveEntry {
                    name: entry_name,
                    source: ArchiveEntrySource::StagedLog(log),
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
    selection::family(name).is_some()
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

fn redact_provider_reasoning_states(session: &mut Value, count: &mut u64) {
    let Some(root) = session.as_object_mut() else {
        return;
    };
    for field in ["events", "active_events"] {
        let Some(events) = root.get_mut(field).and_then(Value::as_array_mut) else {
            continue;
        };
        for event in events {
            let Some(event_object) = event.as_object_mut() else {
                continue;
            };
            if event_object.get("type").and_then(Value::as_str) != Some("provider_reasoning_state")
            {
                continue;
            }
            let Some(payload) = event_object
                .get_mut("payload")
                .and_then(Value::as_object_mut)
            else {
                continue;
            };
            let data = payload.remove("data");
            let data_char_count = data
                .as_ref()
                .and_then(Value::as_str)
                .map(|text| text.chars().count());
            let detail_count = data.as_ref().and_then(Value::as_array).map(Vec::len);
            let mut redacted = serde_json::Map::new();
            for key in ["provider", "model", "format", "replayScope"] {
                if let Some(value) = payload.remove(key) {
                    redacted.insert(key.to_string(), value);
                }
            }
            redacted.insert("redacted".into(), Value::Bool(true));
            if let Some(value) = data_char_count.or_else(|| {
                payload
                    .get("dataCharCount")
                    .and_then(Value::as_u64)
                    .map(|value| value as usize)
            }) {
                redacted.insert("dataCharCount".into(), json!(value));
            }
            if let Some(value) = detail_count.or_else(|| {
                payload
                    .get("detailCount")
                    .and_then(Value::as_u64)
                    .map(|value| value as usize)
            }) {
                redacted.insert("detailCount".into(), json!(value));
            }
            *payload = redacted;
            *count += 1;
        }
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

#[cfg(test)]
fn write_archive_atomically(
    output_dir: &Path,
    base_name: &str,
    entries: Vec<ArchiveEntry>,
) -> Result<PathBuf, String> {
    write_archive_controlled(output_dir, base_name, entries, &job::Control::detached())
}

fn copy_controlled(
    input: &mut impl Read,
    output: &mut impl Write,
    control: &job::Control,
) -> Result<u64, String> {
    let mut buffer = [0_u8; 64 * 1024];
    let mut total = 0;
    loop {
        control.check()?;
        let count = input.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        output
            .write_all(&buffer[..count])
            .map_err(|e| e.to_string())?;
        control.advance(count as u64)?;
        total += count as u64;
    }
    Ok(total)
}

fn write_archive_controlled(
    output_dir: &Path,
    base_name: &str,
    entries: Vec<ArchiveEntry>,
    control: &job::Control,
) -> Result<PathBuf, String> {
    let mut part = scratch::Scratch::archive(output_dir)?.track_cleanup(control);
    {
        let mut writer = ZipWriter::new(&mut part.file);
        let options = SimpleFileOptions::default()
            .compression_method(CompressionMethod::Deflated)
            .large_file(true);
        for mut entry in entries {
            control.check()?;
            if entry.name == "manifest.json" {
                if let ArchiveEntrySource::Bytes(bytes) = &mut entry.source {
                    let mut manifest: Value =
                        serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
                    manifest["exportMetrics"] = control.metrics();
                    manifest["exportMetrics"]["measurementThrough"] = json!("archive entries before manifest; excludes ZIP finish, sync and publication");
                    *bytes = serde_json::to_vec_pretty(&manifest).map_err(|e| e.to_string())?;
                }
            }
            writer
                .start_file(entry.name, options)
                .map_err(|e| format!("start ZIP entry failed: {e}"))?;
            match entry.source {
                ArchiveEntrySource::Bytes(bytes) => {
                    copy_controlled(&mut std::io::Cursor::new(bytes), &mut writer, control)?;
                }
                ArchiveEntrySource::StagedLog(mut log) => {
                    copy_controlled(&mut log.staged.file, &mut writer, control)?;
                }
                ArchiveEntrySource::Scratch(mut file) => {
                    copy_controlled(&mut file.file, &mut writer, control)?;
                }
                ArchiveEntrySource::NativeCrashDump(dump) => {
                    native_crash_export::write_dump_controlled(dump, &mut writer, control)?
                }
            }
        }
        writer
            .finish()
            .map_err(|e| format!("finish ZIP failed: {e}"))?;
    }
    part.file
        .sync_all()
        .map_err(|e| format!("sync ZIP failed: {e}"))?;
    control.publish(|| {
        for suffix in 0..10_000_u32 {
            let name = if suffix == 0 {
                format!("{base_name}.zip")
            } else {
                format!("{base_name}-{suffix}.zip")
            };
            let destination = output_dir.join(name);
            // Publish the finished file without replacing any name on the same filesystem.
            match publish_no_replace(part.path(), &destination) {
                Ok(()) => return Ok(destination),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(format!("publish ZIP failed: {error}")),
            }
        }
        Err("cannot reserve a diagnostic ZIP filename".into())
    })
}

#[cfg(not(windows))]
fn publish_no_replace(source: &Path, destination: &Path) -> std::io::Result<()> {
    fs::hard_link(source, destination)
}

#[cfg(windows)]
fn publish_no_replace(source: &Path, destination: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows::{
        core::PCWSTR,
        Win32::Storage::FileSystem::{MoveFileExW, MOVE_FILE_FLAGS},
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
    // No REPLACE_EXISTING flag: this also works on Windows volumes without hard links.
    unsafe {
        MoveFileExW(
            PCWSTR(source.as_ptr()),
            PCWSTR(destination.as_ptr()),
            MOVE_FILE_FLAGS(0),
        )
    }
    .map_err(|error| std::io::Error::from_raw_os_error(error.code().0 & 0xffff))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn concurrent_publication_never_overwrites_an_existing_bundle() {
        let root = std::env::temp_dir().join(format!("af-publish-{:032x}", rand::random::<u128>()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("same.zip"), b"existing-user-file").unwrap();
        let barrier = Arc::new(std::sync::Barrier::new(2));
        let mut threads = Vec::new();
        for value in ["first", "second"] {
            let dir = root.clone();
            let barrier = barrier.clone();
            threads.push(std::thread::spawn(move || {
                barrier.wait();
                write_archive_atomically(
                    &dir,
                    "same",
                    vec![ArchiveEntry {
                        name: "test.txt".into(),
                        source: ArchiveEntrySource::Bytes(value.as_bytes().to_vec()),
                    }],
                )
                .unwrap()
            }));
        }
        let paths = threads
            .into_iter()
            .map(|worker| worker.join().unwrap())
            .collect::<Vec<_>>();
        assert_ne!(paths[0], paths[1]);
        assert_eq!(
            fs::read(root.join("same.zip")).unwrap(),
            b"existing-user-file"
        );
        for path in paths {
            assert!(zip::ZipArchive::new(File::open(path).unwrap()).is_ok());
        }
        assert!(fs::read_dir(paths::diagnostic_archive_work_dir(&root))
            .unwrap()
            .next()
            .is_none());
        fs::remove_dir_all(root).unwrap();
    }

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
                warnings: &[],
                logs: &logs,
                windows_shell: &windows_shell,
                attachments: &attachments,
                render_evidence: &render_evidence,
                pet_input_evidence: &json!({"status":"not-collected","artifacts":["diagnostics/pet-input/coverage.json"]}),
                selection: &json!({"logs":"recent"}),
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
                    "diagnostics/desktop-instance.json",
                    "diagnostics/execution-environments.json",
                    "diagnostics/command-runtime.json",
                    "diagnostics/command-runtime-incidents.json",
                    "diagnostics/desktop-applications.json",
                    "diagnostics/webview2.json",
                    "diagnostics/webview2-render-evidence.json",
                    "install/integrity.json",
                    "install/attempts/index.json",
                    "diagnostics/attachments.json",
                    "diagnostics/windows-shell/current.json",
                    "diagnostics/pet-input/coverage.json",
                    "diagnostics/pet-render/coverage.json",
                    "diagnostics/native-crash/coverage.json",
                ],
            )
            .expect("current manifest should satisfy bundle contract");
    }

    #[test]
    fn actual_pet_input_producer_zip_is_readable_by_the_offline_consumer() {
        let root = crate::pet_input_evidence::test_temp_dir()
            .join(format!("agent-friend-pet-bundle-{}", rand::random::<u64>()));
        let disk = root.join("pet-input-evidence");
        let manager = pet_input_evidence::PetInputEvidence::start(
            disk.clone(),
            format!("{}-{}", std::process::id(), rand::random::<u32>()),
            json!({"buildMode":"debug","platform":"windows","webviewVersion":"152.0.1"}),
        );
        manager.record(json!({"source":"native","kind":"passthrough-applied",
            "outcome":"error","verified":false,"ignore":false,"stage":"verify",
            "errorKind":"style-verification-failed"}));
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        let export = loop {
            let export = pet_input_evidence::export_for_bundle(&disk);
            if export
                .artifacts
                .iter()
                .any(|a| a.name.starts_with("diagnostics/pet-input/incident-"))
            {
                break export;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "producer did not persist the fixture"
            );
            std::thread::sleep(Duration::from_millis(20));
        };
        manager.shutdown();
        let mut entries = Vec::new();
        let mut failures = Vec::new();
        let pet_summary = append_pet_input_export(export, &mut entries, &mut failures);
        assert!(
            failures.is_empty(),
            "pet input export must succeed: {failures:?}"
        );
        let contract = contract::BundleContract::load().unwrap();
        let render_disk = root.join("pet-render-evidence");
        let render_manager = crate::pet_render_evidence::RenderEvidence::start(
            render_disk.clone(),
            "render-producer-test".into(),
            json!({"buildMode":"debug"}),
        );
        tauri::async_runtime::block_on(render_manager.mark()).unwrap();
        render_manager.shutdown();
        assert!(render_manager.wait_for_shutdown(Duration::from_secs(2)));
        let render_export = crate::pet_render_evidence::journal::export(&render_disk);
        assert!(render_export.failures.is_empty());
        entries.push(
            json_entry(
                "diagnostics/pet-render/coverage.json",
                &render_export.coverage,
            )
            .unwrap(),
        );
        for (name, bytes) in render_export.entries {
            entries.push(ArchiveEntry {
                name,
                source: ArchiveEntrySource::Bytes(bytes),
            });
        }
        // Other evidence families are unrelated fixtures; the pet files above come from the
        // real queue/journal/export producer, not hand-built idealized JSON.
        let definition: Value =
            serde_json::from_str(include_str!("bundle-contract-v1.json")).unwrap();
        let families = std::iter::once(&definition["core"])
            .chain(definition["capabilities"].as_array().unwrap().iter());
        for family in families {
            for entry in family["requiredEntries"].as_array().unwrap() {
                let name = entry.as_str().unwrap();
                if name != "manifest.json" && !entries.iter().any(|e| e.name == name) {
                    entries.push(json_entry(name, &json!({})).unwrap());
                }
            }
        }
        let manifest = build_bundle_manifest(
            &contract,
            BundleManifestInput {
                generated_at: "2026-09-05T00:00:00Z",
                outcome: "complete",
                requested_session_ids: &[],
                included_session_ids: &[],
                failures: &failures,
                warnings: &[],
                logs: &[],
                windows_shell: &json!({}),
                attachments: &json!({}),
                render_evidence: &json!({}),
                pet_input_evidence: &pet_summary,
                selection: &json!({"logs":"recent"}),
            },
        );
        entries.push(json_entry("manifest.json", &manifest).unwrap());
        contract
            .validate_bundle(&manifest, entries.iter().map(|e| e.name.as_str()))
            .unwrap();
        let archive =
            write_archive_atomically(&root, "agent-friend-diagnostics-pet-producer", entries)
                .unwrap();
        let repo = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        let local_python = repo.join(if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python"
        });
        let mut command = if local_python.is_file() {
            std::process::Command::new(local_python)
        } else if cfg!(windows) {
            let mut command = std::process::Command::new("py");
            command.arg("-3");
            command
        } else {
            std::process::Command::new("python3")
        };
        let output =
            command
                .arg(repo.join(
                    ".cursor/skills/inspect-agent-friend-diagnostics/scripts/inspect_bundle.py",
                ))
                .arg(&archive)
                .arg("--json")
                .output()
                .expect("Python is required by the project checks");
        assert!(
            output.status.success(),
            "consumer rejected producer ZIP: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let summary: Value = serde_json::from_slice(&output.stdout).expect("consumer JSON");
        assert_eq!(summary["manifest"]["unhandledCapabilities"], json!([]));
        assert!(
            summary["petRenderEvidence"]["incidentCount"]
                .as_u64()
                .unwrap()
                > 0
        );
        assert!(
            summary["petRenderEvidence"]["historyRecords"]
                .as_u64()
                .unwrap()
                > 0
        );
        assert!(
            !summary["warnings"]
                .as_array()
                .unwrap()
                .iter()
                .any(|w| w.as_str().unwrap_or("").contains("pet render")),
            "{}",
            summary["warnings"]
        );

        assert!(!summary["petInputEvidence"]["retainedIncidents"]
            .as_array()
            .unwrap()
            .is_empty());
        assert!(
            !summary["warnings"]
                .as_array()
                .unwrap()
                .iter()
                .any(|warning| warning.as_str().unwrap_or("").contains("pet input")),
            "pet family warnings: {}",
            summary["warnings"]
        );
        // Wait for the bounded background owner to close before removing the test's exact temp root.
        drop(manager);
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        loop {
            match fs::remove_dir_all(&root) {
                Ok(()) => break,
                Err(_) if std::time::Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(20))
                }
                Err(error) => panic!("remove isolated producer fixture: {error}"),
            }
        }
    }

    #[test]
    fn normalizes_session_ids_without_count_cap() {
        assert_eq!(
            normalize_session_ids(vec![" a ".into(), "a".into(), "b".into()]).unwrap(),
            vec!["a", "b"]
        );
        assert_eq!(
            normalize_session_ids((0..25).map(|i| i.to_string()).collect())
                .unwrap()
                .len(),
            25
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
    fn install_integrity_error_classification_is_stable() {
        assert_eq!(
            classify_install_integrity_status("install_integrity_shell_receipt_mismatch"),
            "shell_receipt_mismatch"
        );
        assert_eq!(
            classify_install_integrity_status("install_integrity_payload_hash_mismatch"),
            "payload_hash_mismatch"
        );
        assert_eq!(
            classify_install_integrity_status("install_integrity_receipt_unreadable"),
            "incomplete_install"
        );
        assert_eq!(classify_install_integrity_status("unknown"), "unavailable");
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
        fs::remove_dir(paths::diagnostic_archive_work_dir(&dir)).unwrap();
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
    fn protocol_error_log_survives_collection_and_zip_redaction() {
        let root = std::env::temp_dir().join(format!("af-protocol-log-{}", rand::random::<u128>()));
        fs::create_dir_all(&root).unwrap();
        let record = json!({
            "call_id": "fixture-call", "stop_reason": "not_retryable",
            "error": {"source": "protocol", "code": "unsupported_stream_event",
                "protocol": {"schema_revision": 1, "event_type": "response.unknown",
                    "event_type_state": "recorded", "stage": "feed", "event_number": 3,
                    "fields": [["type", "string"], ["delta", "string"]],
                    "unknown_field_count": 0, "item_count": 1, "terminal_seen": false}}
        });
        fs::write(
            root.join("agent_bridge.log"),
            format!("model_call_observation {record}\nAuthorization: private-secret\n"),
        )
        .unwrap();
        let collected = collect_logs(&root, &["private-secret".into()], None);
        assert!(collected.failures.is_empty());
        let path = write_archive_atomically(&root, "protocol", collected.entries).unwrap();
        {
            let mut archive = zip::ZipArchive::new(File::open(&path).unwrap()).unwrap();
            let mut text = String::new();
            archive
                .by_name("logs/agent_bridge.log")
                .unwrap()
                .read_to_string(&mut text)
                .unwrap();
            let line = text
                .lines()
                .next()
                .unwrap()
                .strip_prefix("model_call_observation ")
                .unwrap();
            assert_eq!(serde_json::from_str::<Value>(line).unwrap(), record);
            assert!(!text.contains("private-secret"));
        }
        fs::remove_file(path).unwrap();
        fs::remove_file(root.join("agent_bridge.log")).unwrap();
        fs::remove_dir(paths::diagnostic_archive_work_dir(&root)).unwrap();
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn final_archive_contains_no_plaintext_credentials() {
        let dir = std::env::temp_dir().join(format!(
            "agent-friend-redacted-archive-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&dir).unwrap();
        let mut session = json!({
            "events": [
                {
                    "type": "assistant_message",
                    "payload": {
                        "content": "ordinary private conversation",
                        "apiKey": "managed-secret",
                        "accessToken": "other-secret"
                    }
                },
                {
                    "type": "provider_reasoning_state",
                    "payload": {
                        "provider": "kimi",
                        "model": "kimi/kimi-k2.7-code",
                        "format": "reasoning_content",
                        "replayScope": "conversation",
                        "data": "private chain of thought"
                    }
                }
            ]
        });
        let mut count = 0;
        redact_provider_reasoning_states(&mut session, &mut count);
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
        assert!(!contents.contains("private chain of thought"));
        assert!(contents.contains("<REDACTED:CREDENTIAL>"));
        assert!(contents.contains("\"redacted\": true"));
        assert!(contents.contains("\"dataCharCount\": 24"));

        fs::remove_file(path).unwrap();
        fs::remove_dir(paths::diagnostic_archive_work_dir(&dir)).unwrap();
        fs::remove_dir(dir).unwrap();
    }
}
