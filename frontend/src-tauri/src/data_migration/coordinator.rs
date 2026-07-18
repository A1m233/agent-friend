use super::{
    archive::{self, ArchivePayload, ArchiveSource},
    backup, crypto, disk, inventory,
    model::{AssetGroup, Manifest},
};
use crate::{
    paths, runtime_config,
    runtime_services::{RuntimeServiceKind, RuntimeServicePhase, RuntimeServiceSupervisor},
    settings::{self, Settings},
    ui_state::{self, ChatUiPersistence},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap},
    fs,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tauri::{AppHandle, State};
use tokio::sync::Mutex;

pub struct MigrationCoordinator {
    operation: Mutex<()>,
    staged: Mutex<HashMap<String, StagedImport>>,
}

struct StagedImport {
    root: PathBuf,
    manifest: Manifest,
    runtime_secrets: BTreeMap<String, String>,
    im_credentials: serde_json::Value,
}

impl Default for MigrationCoordinator {
    fn default() -> Self {
        Self {
            operation: Mutex::new(()),
            staged: Mutex::new(HashMap::new()),
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationInventory {
    pub file_count: usize,
    pub total_bytes: u64,
    pub session_file_count: usize,
    pub memory_file_count: usize,
    pub persona_file_count: usize,
    pub user_model_file_count: usize,
    pub managed_credential_count: usize,
    pub im_credential_count: usize,
    pub semantic_memory_count: usize,
    pub episodic_memory_count: usize,
    pub persona_modified: bool,
    pub has_meaningful_data: bool,
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BridgeMigrationInventory {
    sessions: usize,
    semantic_memories: usize,
    episodic_memories: usize,
    persona_modified: bool,
    im_credentials: usize,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportMigrationPayload {
    pub output_path: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportMigrationResult {
    pub transfer_code: String,
    pub output_path: String,
    pub manifest: Manifest,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PreflightMigrationPayload {
    pub input_path: String,
    pub transfer_code: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreflightMigrationResult {
    pub operation_id: String,
    pub manifest: Manifest,
    pub destination: MigrationInventory,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CommitMigrationPayload {
    pub operation_id: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationAssetResult {
    pub group: String,
    pub status: String,
    pub item_count: usize,
    pub message: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MigrationCommitResult {
    pub completed_at: String,
    pub assets: Vec<MigrationAssetResult>,
}

#[tauri::command]
pub async fn get_migration_inventory(
    app: AppHandle,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
    settings_state: State<'_, Arc<std::sync::Mutex<Settings>>>,
) -> Result<MigrationInventory, String> {
    ensure_supported_platform()?;
    ensure_bridge_ready(&app, &supervisor).await?;
    let settings_modified = settings::migration_snapshot(&settings_state)? != Settings::default();
    inventory_snapshot(
        Some(read_bridge_inventory(&supervisor).await?),
        settings_modified,
    )
}

#[tauri::command]
pub async fn export_migration_package(
    app: AppHandle,
    coordinator: State<'_, Arc<MigrationCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
    settings_state: State<'_, Arc<std::sync::Mutex<Settings>>>,
    payload: ExportMigrationPayload,
) -> Result<ExportMigrationResult, String> {
    ensure_supported_platform()?;
    let _operation = coordinator
        .operation
        .try_lock()
        .map_err(|_| "migration_operation_in_progress".to_string())?;
    let output = PathBuf::from(&payload.output_path);
    if output.extension().and_then(|value| value.to_str()) != Some("agentfriend-transfer") {
        return Err("migration_output_extension_invalid".to_string());
    }
    let settings = settings::migration_snapshot(&settings_state)?;
    let ui_continuity = ui_state::migration_snapshot(&app)?;
    let runtime_secrets = runtime_config::migration_snapshot_managed_secrets()?;
    let bridge_before = supervisor.snapshot(RuntimeServiceKind::Bridge).await;
    let voice_before = supervisor.snapshot(RuntimeServiceKind::Voice).await;
    if bridge_before.phase != RuntimeServicePhase::Ready {
        supervisor
            .start(Some(&app), RuntimeServiceKind::Bridge)
            .await?;
    }
    let im_credentials = match supervisor.migration_snapshot_im_credentials().await {
        Ok(value) => value,
        Err(error) => {
            let _ =
                restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase)
                    .await;
            return Err(error);
        }
    };
    if voice_before.phase == RuntimeServicePhase::Ready {
        if let Err(error) = supervisor.stop(Some(&app), RuntimeServiceKind::Voice).await {
            let _ =
                restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase)
                    .await;
            return Err(error);
        }
    }
    if let Err(error) = supervisor
        .stop(Some(&app), RuntimeServiceKind::Bridge)
        .await
    {
        let _ =
            restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
        return Err(error);
    }

    let transfer_code = crypto::generate_transfer_code()?;
    let code_for_export = transfer_code.clone();
    let output_for_export = output.clone();
    let export_result = tokio::task::spawn_blocking(move || {
        let file_name = output_for_export
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or_else(|| "migration_output_path_invalid".to_string())?;
        let candidate = output_for_export.with_file_name(format!(
            ".{file_name}.{:016x}.candidate",
            rand::random::<u64>()
        ));
        let mut sources = inventory::collect_file_assets()?;
        sources.push(json_source(
            "state/settings.json",
            AssetGroup::Settings,
            &settings,
        )?);
        sources.push(json_source(
            "state/ui-continuity.json",
            AssetGroup::UiContinuity,
            &ui_continuity,
        )?);
        sources.push(json_source(
            "secrets/runtime.json",
            AssetGroup::RuntimeCredentials,
            &runtime_secrets,
        )?);
        sources.push(json_source(
            "secrets/im.json",
            AssetGroup::ImCredentials,
            &im_credentials,
        )?);
        let mut manifest = None;
        let encrypted = crypto::encrypt_with(&candidate, &code_for_export, |writer| {
            manifest = Some(archive::create_archive(writer, &sources)?);
            Ok(())
        });
        if let Err(error) = encrypted {
            let _ = fs::remove_file(&candidate);
            return Err(error);
        }
        let manifest = manifest.ok_or_else(|| "migration_manifest_missing".to_string())?;
        let mut verified = None;
        let verification = crypto::decrypt_with(&candidate, &code_for_export, |reader| {
            verified = Some(archive::verify_archive(reader)?);
            Ok(())
        });
        if let Err(error) = verification {
            let _ = fs::remove_file(&candidate);
            return Err(error);
        }
        if verified.as_ref() != Some(&manifest) {
            let _ = fs::remove_file(&candidate);
            return Err("migration_export_verification_failed".to_string());
        }
        if let Err(error) = replace_verified_file(&candidate, &output_for_export) {
            let _ = fs::remove_file(&candidate);
            return Err(error);
        }
        Ok(manifest)
    })
    .await
    .map_err(|error| format!("migration_export_task_failed: {error}"))
    .and_then(|result| result);

    let restore_result =
        restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
    let manifest = export_result?;
    restore_result?;
    Ok(ExportMigrationResult {
        transfer_code,
        output_path: output.to_string_lossy().into_owned(),
        manifest,
    })
}

#[tauri::command]
pub async fn preflight_migration_package(
    app: AppHandle,
    coordinator: State<'_, Arc<MigrationCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
    settings_state: State<'_, Arc<std::sync::Mutex<Settings>>>,
    payload: PreflightMigrationPayload,
) -> Result<PreflightMigrationResult, String> {
    ensure_supported_platform()?;
    let _operation = coordinator
        .operation
        .try_lock()
        .map_err(|_| "migration_operation_in_progress".to_string())?;
    let operation_id = format!(
        "{}-{:016x}",
        chrono::Utc::now().timestamp_millis(),
        rand::random::<u64>()
    );
    let root = paths::migration_work_dir().join(&operation_id);
    fs::create_dir_all(&root)
        .map_err(|error| format!("migration_staging_create_failed: {error}"))?;
    let input = PathBuf::from(payload.input_path);
    let code = payload.transfer_code;
    let staging = root.join("staging");
    let extraction = tokio::task::spawn_blocking(move || {
        let mut extracted = None;
        crypto::decrypt_with(&input, &code, |reader| {
            extracted = Some(archive::extract_migration_archive(reader, &staging)?);
            Ok(())
        })?;
        extracted.ok_or_else(|| "migration_manifest_missing".to_string())
    })
    .await
    .map_err(|error| format!("migration_preflight_task_failed: {error}"))?;
    let mut extracted = match extraction {
        Ok(extracted) => extracted,
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    let imported_secrets = take_secret_json(&mut extracted.secrets, "secrets/runtime.json")?;
    let imported_im = take_secret_json(&mut extracted.secrets, "secrets/im.json")?;
    let manifest = extracted.manifest;
    if let Err(error) = validate_manifest_semantics(&manifest) {
        let _ = fs::remove_dir_all(&root);
        return Err(error);
    }
    if let Err(error) = ensure_bridge_ready(&app, &supervisor).await {
        let _ = fs::remove_dir_all(&root);
        return Err(error);
    }
    let bridge_inventory = match read_bridge_inventory(&supervisor).await {
        Ok(value) => value,
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    let settings_modified = match settings::migration_snapshot(&settings_state) {
        Ok(value) => value != Settings::default(),
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    let destination = match inventory_snapshot(Some(bridge_inventory), settings_modified) {
        Ok(value) => value,
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    if let Err(error) =
        disk::ensure_available_space(&paths::user_data_dir(), destination.total_bytes)
    {
        let _ = fs::remove_dir_all(&root);
        return Err(error);
    }
    replace_staged_import(
        &coordinator,
        &operation_id,
        &root,
        &manifest,
        imported_secrets,
        imported_im,
    )
    .await;
    Ok(PreflightMigrationResult {
        operation_id,
        manifest,
        destination,
    })
}

#[tauri::command]
pub async fn preflight_latest_migration_backup(
    app: AppHandle,
    coordinator: State<'_, Arc<MigrationCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
    settings_state: State<'_, Arc<std::sync::Mutex<Settings>>>,
) -> Result<PreflightMigrationResult, String> {
    ensure_supported_platform()?;
    let _operation = coordinator
        .operation
        .try_lock()
        .map_err(|_| "migration_operation_in_progress".to_string())?;
    let operation_id = format!(
        "{}-{:016x}",
        chrono::Utc::now().timestamp_millis(),
        rand::random::<u64>()
    );
    let root = paths::migration_work_dir().join(&operation_id);
    let staging = root.join("staging");
    fs::create_dir_all(&staging)
        .map_err(|error| format!("migration_staging_create_failed: {error}"))?;
    let extraction = tokio::task::spawn_blocking(move || backup::extract_latest(&staging))
        .await
        .map_err(|error| format!("migration_backup_preflight_task_failed: {error}"))?;
    let mut extracted = match extraction {
        Ok(extracted) => extracted,
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    let imported_secrets = take_secret_json(&mut extracted.secrets, "secrets/runtime.json")?;
    let imported_im = take_secret_json(&mut extracted.secrets, "secrets/im.json")?;
    let manifest = extracted.manifest;
    if let Err(error) = validate_manifest_semantics(&manifest) {
        let _ = fs::remove_dir_all(&root);
        return Err(error);
    }
    if let Err(error) = ensure_bridge_ready(&app, &supervisor).await {
        let _ = fs::remove_dir_all(&root);
        return Err(error);
    }
    let bridge_inventory = match read_bridge_inventory(&supervisor).await {
        Ok(value) => value,
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    let settings_modified = match settings::migration_snapshot(&settings_state) {
        Ok(value) => value != Settings::default(),
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    let destination = match inventory_snapshot(Some(bridge_inventory), settings_modified) {
        Ok(value) => value,
        Err(error) => {
            let _ = fs::remove_dir_all(&root);
            return Err(error);
        }
    };
    if let Err(error) =
        disk::ensure_available_space(&paths::user_data_dir(), destination.total_bytes)
    {
        let _ = fs::remove_dir_all(&root);
        return Err(error);
    }
    replace_staged_import(
        &coordinator,
        &operation_id,
        &root,
        &manifest,
        imported_secrets,
        imported_im,
    )
    .await;
    Ok(PreflightMigrationResult {
        operation_id,
        manifest,
        destination,
    })
}

#[tauri::command]
pub async fn commit_migration_package(
    app: AppHandle,
    coordinator: State<'_, Arc<MigrationCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
    settings_state: State<'_, Arc<std::sync::Mutex<Settings>>>,
    payload: CommitMigrationPayload,
) -> Result<MigrationCommitResult, String> {
    ensure_supported_platform()?;
    let _operation = coordinator
        .operation
        .try_lock()
        .map_err(|_| "migration_operation_in_progress".to_string())?;
    let staged = coordinator
        .staged
        .lock()
        .await
        .remove(&payload.operation_id)
        .ok_or_else(|| "migration_operation_not_found".to_string())?;
    let StagedImport {
        root,
        manifest,
        runtime_secrets: imported_secrets,
        im_credentials: imported_im,
    } = staged;
    validate_manifest_semantics(&manifest)?;
    let staging = root.join("staging");
    let imported_settings: Settings = read_json(&staging.join("state/settings.json"))?;
    let imported_ui: ChatUiPersistence = read_json(&staging.join("state/ui-continuity.json"))?;
    let previous_settings = settings::migration_snapshot(&settings_state)?;
    let previous_ui = ui_state::migration_snapshot(&app)?;
    let previous_secrets = runtime_config::migration_snapshot_managed_secrets()?;
    let bridge_before = supervisor.snapshot(RuntimeServiceKind::Bridge).await;
    let voice_before = supervisor.snapshot(RuntimeServiceKind::Voice).await;
    if bridge_before.phase != RuntimeServicePhase::Ready {
        supervisor
            .start(Some(&app), RuntimeServiceKind::Bridge)
            .await?;
    }
    let previous_im = match supervisor.migration_snapshot_im_credentials().await {
        Ok(value) => value,
        Err(error) => {
            let _ =
                restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase)
                    .await;
            return Err(error);
        }
    };
    if voice_before.phase == RuntimeServicePhase::Ready {
        if let Err(error) = supervisor.stop(Some(&app), RuntimeServiceKind::Voice).await {
            let _ =
                restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase)
                    .await;
            return Err(error);
        }
    }
    if let Err(error) = supervisor
        .stop(Some(&app), RuntimeServiceKind::Bridge)
        .await
    {
        let _ =
            restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
        return Err(error);
    }

    if let Err(error) = backup::create_latest(
        &previous_settings,
        &previous_ui,
        &previous_secrets,
        &previous_im,
        "beforeMigrationImport",
    ) {
        let _ =
            restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
        return Err(error);
    }
    let old = root.join("old");
    if let Err(error) = fs::create_dir_all(&old) {
        let _ =
            restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
        return Err(format!("migration_transaction_prepare_failed: {error}"));
    }
    let journal = root.join("journal.json");
    if let Err(error) = write_journal(&journal, "filesSwapping") {
        let _ =
            restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
        return Err(error);
    }
    let mut files_swapped = false;
    let mut settings_applied = false;
    let mut ui_applied = false;
    let mut secrets_applied = false;
    let result = (|| -> Result<(), String> {
        swap_managed_assets(&staging.join("assets"), &old)?;
        files_swapped = true;
        write_journal(&journal, "settingsApplied")?;
        settings::migration_replace(&app, &settings_state, imported_settings)?;
        settings_applied = true;
        ui_state::migration_replace(&app, imported_ui)?;
        ui_applied = true;
        runtime_config::migration_replace_managed_secrets(&imported_secrets)?;
        secrets_applied = true;
        Ok(())
    })();
    if let Err(error) = result {
        rollback_local(
            &app,
            &settings_state,
            &previous_settings,
            &previous_ui,
            &previous_secrets,
            &old,
            files_swapped,
            settings_applied,
            ui_applied,
            secrets_applied,
        );
        let _ =
            restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
        return Err(error);
    }

    let finalize_result = async {
        write_journal(&journal, "servicesStarting")?;
        supervisor
            .start(Some(&app), RuntimeServiceKind::Bridge)
            .await?;
        supervisor.migration_rebuild_memory_index().await?;
        supervisor
            .migration_replace_im_credentials(&imported_im)
            .await?;
        write_journal(&journal, "credentialsApplied")?;
        write_journal(&journal, "completed")?;
        Ok::<(), String>(())
    }
    .await;
    if let Err(error) = finalize_result {
        let _ = supervisor
            .stop(Some(&app), RuntimeServiceKind::Bridge)
            .await;
        rollback_local(
            &app,
            &settings_state,
            &previous_settings,
            &previous_ui,
            &previous_secrets,
            &old,
            true,
            true,
            true,
            true,
        );
        let _ = supervisor
            .start(Some(&app), RuntimeServiceKind::Bridge)
            .await;
        let _ = supervisor.migration_rebuild_memory_index().await;
        let _ = supervisor
            .migration_replace_im_credentials(&previous_im)
            .await;
        let _ =
            restore_runtime_state(&app, &supervisor, bridge_before.phase, voice_before.phase).await;
        return Err(error);
    }
    let _ = fs::remove_dir_all(&old);
    let _ = fs::remove_file(&journal);
    if voice_before.phase == RuntimeServicePhase::Ready {
        if let Err(error) = supervisor
            .start(Some(&app), RuntimeServiceKind::Voice)
            .await
        {
            log::warn!("migration: voice runtime restore failed after commit: {error}");
        }
    }
    let commit_result = build_commit_result(&manifest, &imported_secrets, &imported_im);
    match serde_json::to_value(&commit_result)
        .map_err(|error| format!("migration_result_serialize_failed: {error}"))
        .and_then(|value| ui_state::set_migration_result(&app, value))
    {
        Ok(()) => {}
        Err(error) => log::warn!("migration: result persistence failed: {error}"),
    }
    let restart = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(500)).await;
        let _ = fs::remove_dir_all(root);
        restart.restart();
    });
    Ok(commit_result)
}

#[tauri::command]
pub fn get_latest_migration_backup() -> Result<Option<backup::BackupMetadata>, String> {
    ensure_supported_platform()?;
    backup::latest_metadata()
}

#[tauri::command]
pub fn delete_latest_migration_backup() -> Result<(), String> {
    ensure_supported_platform()?;
    backup::delete_latest()
}

#[tauri::command]
pub fn get_last_migration_result(app: AppHandle) -> Result<Option<serde_json::Value>, String> {
    ui_state::migration_result(&app)
}

#[tauri::command]
pub fn clear_last_migration_result(app: AppHandle) -> Result<(), String> {
    ui_state::clear_migration_result(&app)
}

#[cfg(target_os = "windows")]
fn ensure_supported_platform() -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "windows"))]
fn ensure_supported_platform() -> Result<(), String> {
    Err("migration_platform_unsupported: Windows is required".to_string())
}

pub async fn recover_incomplete_transaction(
    app: &AppHandle,
    supervisor: &RuntimeServiceSupervisor,
    settings_state: &Arc<std::sync::Mutex<Settings>>,
) -> Result<bool, String> {
    let work_root = paths::migration_work_dir();
    if !work_root.exists() {
        return Ok(false);
    }
    let mut pending = fs::read_dir(&work_root)
        .map_err(|error| format!("migration_recovery_scan_failed: {error}"))?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.join("journal.json").is_file())
        .collect::<Vec<_>>();
    pending.sort();
    let Some(operation_root) = pending.pop() else {
        cleanup_orphaned_staging(&work_root, None);
        return Ok(false);
    };

    let recovery_staging = operation_root.join("recovery-staging");
    let discarded = operation_root.join("recovery-discarded");
    let _ = fs::remove_dir_all(&recovery_staging);
    let _ = fs::remove_dir_all(&discarded);
    fs::create_dir_all(&recovery_staging)
        .map_err(|error| format!("migration_recovery_prepare_failed: {error}"))?;
    let mut extracted = backup::extract_latest(&recovery_staging)?;
    validate_manifest_semantics(&extracted.manifest)?;
    let restored_settings: Settings = read_json(&recovery_staging.join("state/settings.json"))?;
    let restored_ui: ChatUiPersistence =
        read_json(&recovery_staging.join("state/ui-continuity.json"))?;
    let restored_secrets = take_secret_json(&mut extracted.secrets, "secrets/runtime.json")?;
    let restored_im = take_secret_json(&mut extracted.secrets, "secrets/im.json")?;

    let _ = supervisor.stop(Some(app), RuntimeServiceKind::Voice).await;
    let _ = supervisor.stop(Some(app), RuntimeServiceKind::Bridge).await;
    swap_managed_assets(&recovery_staging.join("assets"), &discarded)?;
    settings::migration_replace(app, settings_state, restored_settings)?;
    ui_state::migration_replace(app, restored_ui)?;
    runtime_config::migration_replace_managed_secrets(&restored_secrets)?;
    supervisor
        .start(Some(app), RuntimeServiceKind::Bridge)
        .await?;
    supervisor.migration_rebuild_memory_index().await?;
    supervisor
        .migration_replace_im_credentials(&restored_im)
        .await?;

    fs::remove_dir_all(&operation_root)
        .map_err(|error| format!("migration_recovery_cleanup_failed: {error}"))?;
    for stale in pending {
        let _ = fs::remove_dir_all(stale);
    }
    cleanup_orphaned_staging(&work_root, None);
    Ok(true)
}

async fn replace_staged_import(
    coordinator: &MigrationCoordinator,
    operation_id: &str,
    root: &Path,
    manifest: &Manifest,
    runtime_secrets: BTreeMap<String, String>,
    im_credentials: serde_json::Value,
) {
    let mut staged = coordinator.staged.lock().await;
    for (_, previous) in staged.drain() {
        let _ = fs::remove_dir_all(previous.root);
    }
    staged.insert(
        operation_id.to_string(),
        StagedImport {
            root: root.to_path_buf(),
            manifest: manifest.clone(),
            runtime_secrets,
            im_credentials,
        },
    );
}

fn take_secret_json<T: for<'de> Deserialize<'de>>(
    secrets: &mut HashMap<String, Vec<u8>>,
    path: &str,
) -> Result<T, String> {
    let bytes = secrets
        .remove(path)
        .ok_or_else(|| format!("migration_secret_missing: {path}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("migration_secret_invalid: {error}"))
}

fn cleanup_orphaned_staging(work_root: &Path, keep: Option<&Path>) {
    let Ok(entries) = fs::read_dir(work_root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if keep.is_some_and(|value| value == path) || path.join("journal.json").is_file() {
            continue;
        }
        let _ = fs::remove_dir_all(path);
    }
}

fn inventory_snapshot(
    bridge: Option<BridgeMigrationInventory>,
    settings_modified: bool,
) -> Result<MigrationInventory, String> {
    let sources = inventory::collect_file_assets()?;
    let count = |group| {
        sources
            .iter()
            .filter(|source| source.group == group)
            .count()
    };
    let total_bytes = sources
        .iter()
        .map(|source| match &source.source {
            ArchivePayload::File(path) => path.metadata().map(|value| value.len()).unwrap_or(0),
            ArchivePayload::Bytes(bytes) => bytes.len() as u64,
        })
        .sum();
    let managed_credential_count = runtime_config::migration_snapshot_managed_secrets()?.len();
    let session_file_count = count(AssetGroup::Sessions);
    let memory_file_count = count(AssetGroup::Memory);
    let persona_file_count = count(AssetGroup::Persona);
    let user_model_file_count = count(AssetGroup::UserAvatarModels);
    let file_count = sources.len();
    let bridge = bridge.unwrap_or_default();
    let cli_history_present = sources
        .iter()
        .any(|source| source.group == AssetGroup::CliHistory);
    let has_meaningful_data = bridge.sessions > 0
        || bridge.semantic_memories > 0
        || bridge.episodic_memories > 0
        || bridge.persona_modified
        || bridge.im_credentials > 0
        || settings_modified
        || user_model_file_count > 0
        || managed_credential_count > 0
        || cli_history_present;
    Ok(MigrationInventory {
        file_count,
        total_bytes,
        session_file_count,
        memory_file_count,
        persona_file_count,
        user_model_file_count,
        managed_credential_count,
        im_credential_count: bridge.im_credentials,
        semantic_memory_count: bridge.semantic_memories,
        episodic_memory_count: bridge.episodic_memories,
        persona_modified: bridge.persona_modified,
        has_meaningful_data,
    })
}

async fn ensure_bridge_ready(
    app: &AppHandle,
    supervisor: &RuntimeServiceSupervisor,
) -> Result<(), String> {
    if supervisor.snapshot(RuntimeServiceKind::Bridge).await.phase != RuntimeServicePhase::Ready {
        supervisor
            .start(Some(app), RuntimeServiceKind::Bridge)
            .await?;
    }
    Ok(())
}

async fn read_bridge_inventory(
    supervisor: &RuntimeServiceSupervisor,
) -> Result<BridgeMigrationInventory, String> {
    let value = supervisor.migration_inventory().await?;
    serde_json::from_value(value).map_err(|error| format!("migration_inventory_invalid: {error}"))
}

fn json_source(
    archive_path: &str,
    group: AssetGroup,
    value: &impl Serialize,
) -> Result<ArchiveSource, String> {
    Ok(ArchiveSource {
        source: ArchivePayload::Bytes(
            serde_json::to_vec(value)
                .map_err(|error| format!("migration_payload_serialize_failed: {error}"))?,
        ),
        archive_path: archive_path.to_string(),
        group,
    })
}

fn replace_verified_file(candidate: &Path, target: &Path) -> Result<(), String> {
    let previous = target.with_file_name(format!(
        ".{}.previous-{:016x}",
        target
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("migration-package"),
        rand::random::<u64>()
    ));
    if target.exists() {
        fs::rename(target, &previous)
            .map_err(|error| format!("migration_output_replace_prepare_failed: {error}"))?;
    }
    if let Err(error) = fs::rename(candidate, target) {
        if previous.exists() && !target.exists() {
            let _ = fs::rename(&previous, target);
        }
        return Err(format!("migration_output_replace_failed: {error}"));
    }
    let _ = fs::remove_file(previous);
    Ok(())
}

fn build_commit_result(
    manifest: &Manifest,
    imported_secrets: &BTreeMap<String, String>,
    imported_im: &serde_json::Value,
) -> MigrationCommitResult {
    let count = |group| {
        manifest
            .entries
            .iter()
            .filter(|entry| entry.group == group)
            .count()
    };
    let im_count = imported_im
        .get("credentials")
        .and_then(serde_json::Value::as_array)
        .map_or(0, Vec::len);
    let assets = [
        ("sessions", count(AssetGroup::Sessions), "历史会话已恢复"),
        (
            "memory",
            count(AssetGroup::Memory),
            "长期记忆已恢复并重建索引",
        ),
        (
            "persona",
            count(AssetGroup::Persona),
            "人格与内部状态已恢复",
        ),
        ("settings", count(AssetGroup::Settings), "持久设置已恢复"),
        (
            "userModels",
            count(AssetGroup::UserAvatarModels),
            "用户桌宠模型已恢复",
        ),
        (
            "apiCredentials",
            imported_secrets.len(),
            "API 凭据已写入系统凭证管理器",
        ),
        (
            "imCredentials",
            im_count,
            "QQ 本地凭据已按新设备重新加密；平台可用性将在连接时验证",
        ),
    ]
    .into_iter()
    .map(|(group, item_count, message)| MigrationAssetResult {
        group: group.to_string(),
        status: if item_count == 0 {
            "notPresent"
        } else {
            "success"
        }
        .to_string(),
        item_count,
        message: message.to_string(),
    })
    .collect();
    MigrationCommitResult {
        completed_at: chrono::Utc::now().to_rfc3339(),
        assets,
    }
}

async fn restore_runtime_state(
    app: &AppHandle,
    supervisor: &RuntimeServiceSupervisor,
    bridge: RuntimeServicePhase,
    voice: RuntimeServicePhase,
) -> Result<(), String> {
    if bridge == RuntimeServicePhase::Ready {
        supervisor
            .start(Some(app), RuntimeServiceKind::Bridge)
            .await?;
    }
    if voice == RuntimeServicePhase::Ready {
        supervisor
            .start(Some(app), RuntimeServiceKind::Voice)
            .await?;
    }
    Ok(())
}

fn validate_manifest_semantics(manifest: &Manifest) -> Result<(), String> {
    let source_version = parse_app_version(&manifest.source_app_version)?;
    let current_version = parse_app_version(env!("CARGO_PKG_VERSION"))?;
    if source_version > current_version {
        return Err("package_requires_newer_app".to_string());
    }
    for entry in &manifest.entries {
        let valid = match entry.group {
            AssetGroup::Sessions => entry.path.starts_with("assets/sessions/"),
            AssetGroup::Memory => entry.path.starts_with("assets/memory/"),
            AssetGroup::Persona => entry.path.starts_with("assets/personas/"),
            AssetGroup::Settings => entry.path == "state/settings.json",
            AssetGroup::RuntimeCredentials => entry.path == "secrets/runtime.json",
            AssetGroup::ImCredentials => entry.path == "secrets/im.json",
            AssetGroup::UserAvatarModels => {
                entry.path.starts_with("assets/avatar-models/")
                    && !entry.path.starts_with("assets/avatar-models/builtin/")
            }
            AssetGroup::CliHistory => entry.path == "assets/.cli_history",
            AssetGroup::UiContinuity => entry.path == "state/ui-continuity.json",
        };
        if !valid {
            return Err("archive_asset_policy_rejected".to_string());
        }
    }
    for required in [
        "state/settings.json",
        "state/ui-continuity.json",
        "secrets/runtime.json",
        "secrets/im.json",
    ] {
        if !manifest.entries.iter().any(|entry| entry.path == required) {
            return Err(format!("archive_required_asset_missing: {required}"));
        }
    }
    Ok(())
}

fn parse_app_version(value: &str) -> Result<(u64, u64, u64), String> {
    let core = value.split_once('-').map_or(value, |(core, _)| core);
    let mut parts = core.split('.');
    let major = parts
        .next()
        .and_then(|part| part.parse().ok())
        .ok_or_else(|| "package_app_version_invalid".to_string())?;
    let minor = parts
        .next()
        .and_then(|part| part.parse().ok())
        .ok_or_else(|| "package_app_version_invalid".to_string())?;
    let patch = parts
        .next()
        .and_then(|part| part.parse().ok())
        .ok_or_else(|| "package_app_version_invalid".to_string())?;
    if parts.next().is_some() {
        return Err("package_app_version_invalid".to_string());
    }
    Ok((major, minor, patch))
}

fn swap_managed_assets(staging: &Path, old: &Path) -> Result<(), String> {
    let root = paths::user_data_dir();
    for name in ["sessions", "memory", "personas"] {
        rotate_path(&root.join(name), &staging.join(name), &old.join(name))?;
    }
    rotate_path(
        &root.join(".cli_history"),
        &staging.join(".cli_history"),
        &old.join(".cli_history"),
    )?;

    let active_models = root.join("avatar-models");
    let staged_models = staging.join("avatar-models");
    let old_models = old.join("avatar-models");
    fs::create_dir_all(&active_models)
        .map_err(|error| format!("migration_avatar_prepare_failed: {error}"))?;
    fs::create_dir_all(&old_models)
        .map_err(|error| format!("migration_avatar_backup_failed: {error}"))?;
    for entry in fs::read_dir(&active_models)
        .map_err(|error| format!("migration_avatar_read_failed: {error}"))?
    {
        let entry = entry.map_err(|error| format!("migration_avatar_read_failed: {error}"))?;
        if entry.file_name() != "builtin" {
            fs::rename(entry.path(), old_models.join(entry.file_name()))
                .map_err(|error| format!("migration_avatar_swap_failed: {error}"))?;
        }
    }
    if staged_models.exists() {
        for entry in fs::read_dir(staged_models)
            .map_err(|error| format!("migration_avatar_staging_read_failed: {error}"))?
        {
            let entry =
                entry.map_err(|error| format!("migration_avatar_staging_read_failed: {error}"))?;
            if entry.file_name() == "builtin" {
                return Err("migration_builtin_avatar_rejected".to_string());
            }
            fs::rename(entry.path(), active_models.join(entry.file_name()))
                .map_err(|error| format!("migration_avatar_commit_failed: {error}"))?;
        }
    }
    Ok(())
}

fn rotate_path(active: &Path, staged: &Path, old: &Path) -> Result<(), String> {
    if active.exists() {
        if let Some(parent) = old.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("migration_old_prepare_failed: {error}"))?;
        }
        fs::rename(active, old).map_err(|error| format!("migration_old_rotate_failed: {error}"))?;
    }
    if staged.exists() {
        if let Some(parent) = active.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("migration_active_prepare_failed: {error}"))?;
        }
        fs::rename(staged, active)
            .map_err(|error| format!("migration_active_commit_failed: {error}"))?;
    }
    Ok(())
}

fn rollback_local(
    app: &AppHandle,
    settings_state: &Arc<std::sync::Mutex<Settings>>,
    previous_settings: &Settings,
    previous_ui: &ChatUiPersistence,
    previous_secrets: &BTreeMap<String, String>,
    old: &Path,
    files_swapped: bool,
    settings_applied: bool,
    ui_applied: bool,
    secrets_applied: bool,
) {
    if secrets_applied {
        let _ = runtime_config::migration_restore_managed_secrets(previous_secrets);
    }
    if settings_applied {
        let _ = settings::migration_replace(app, settings_state, previous_settings.clone());
    }
    if files_swapped {
        let root = paths::user_data_dir();
        for name in ["sessions", "memory", "personas", ".cli_history"] {
            let active = root.join(name);
            if active.is_dir() {
                let _ = fs::remove_dir_all(&active);
            } else {
                let _ = fs::remove_file(&active);
            }
            let previous = old.join(name);
            if previous.exists() {
                let _ = fs::rename(previous, active);
            }
        }
        let active_models = root.join("avatar-models");
        if let Ok(entries) = fs::read_dir(&active_models) {
            for entry in entries.flatten() {
                if entry.file_name() != "builtin" {
                    let path = entry.path();
                    if path.is_dir() {
                        let _ = fs::remove_dir_all(path);
                    } else {
                        let _ = fs::remove_file(path);
                    }
                }
            }
        }
        let old_models = old.join("avatar-models");
        if let Ok(entries) = fs::read_dir(old_models) {
            for entry in entries.flatten() {
                let _ = fs::rename(entry.path(), active_models.join(entry.file_name()));
            }
        }
    }
    if ui_applied {
        let _ = ui_state::migration_replace(app, previous_ui.clone());
    }
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    let bytes =
        fs::read(path).map_err(|error| format!("migration_payload_read_failed: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("migration_payload_invalid: {error}"))
}

fn write_journal(path: &Path, phase: &str) -> Result<(), String> {
    let partial = path.with_extension("part");
    fs::write(
        &partial,
        serde_json::to_vec(&serde_json::json!({
            "schemaVersion": 1,
            "phase": phase,
            "updatedAt": chrono::Utc::now().to_rfc3339(),
        }))
        .map_err(|error| format!("migration_journal_serialize_failed: {error}"))?,
    )
    .map_err(|error| format!("migration_journal_write_failed: {error}"))?;
    fs::rename(partial, path).map_err(|error| format!("migration_journal_commit_failed: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn future_app_versions_are_rejected_before_commit() {
        let mut manifest = Manifest::new(Vec::new());
        manifest.source_app_version = "999.0.0".to_string();
        assert_eq!(
            validate_manifest_semantics(&manifest),
            Err("package_requires_newer_app".to_string())
        );
    }

    #[test]
    fn app_version_parser_rejects_ambiguous_values() {
        assert_eq!(parse_app_version("0.1.5"), Ok((0, 1, 5)));
        assert!(parse_app_version("0.1").is_err());
        assert!(parse_app_version("0.1.5.1").is_err());
    }
}
