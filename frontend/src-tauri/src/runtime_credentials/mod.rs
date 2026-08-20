use crate::{
    paths,
    runtime_config::{self, RuntimeConfigGroup, RuntimeConfigItemStatus, RuntimeConfigSource},
    runtime_services::{RuntimeServiceKind, RuntimeServiceSupervisor},
};
use chrono::Utc;
use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs,
    path::Path,
    sync::Arc,
};
use tauri::{AppHandle, Emitter, State};
use tokio::sync::Mutex;

mod model;
use model::*;

const EVENT_CHANGED: &str = "runtime-credential://changed";
const STATE_SCHEMA_VERSION: u32 = 1;
const STAGE_PREFIX: &str = "__agent_friend_runtime_credential_stage__";
const REVISION_PREFIX: &str = "__agent_friend_runtime_credential_revision__";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
enum PersistedValidationStatus {
    Valid,
    Invalid,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedValidationRecord {
    revision: String,
    status: PersistedValidationStatus,
    checked_at: String,
    safe_code: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeCredentialStateFile {
    version: u32,
    groups: BTreeMap<String, PersistedValidationRecord>,
}

impl Default for RuntimeCredentialStateFile {
    fn default() -> Self {
        Self {
            version: STATE_SCHEMA_VERSION,
            groups: BTreeMap::new(),
        }
    }
}

#[derive(Clone, Debug)]
struct PendingFieldMutation {
    name: String,
    action: RuntimeCredentialFieldAction,
}

#[derive(Clone, Debug)]
struct PendingOperation {
    operation_id: String,
    group: RuntimeConfigGroup,
    changes: Vec<PendingFieldMutation>,
    validation: Option<PersistedValidationStatus>,
    validation_code: Option<String>,
}

pub struct RuntimeCredentialCoordinator {
    group_locks: HashMap<RuntimeConfigGroup, Arc<Mutex<()>>>,
    operations: Mutex<HashMap<RuntimeConfigGroup, RuntimeCredentialOperation>>,
    pending: Mutex<HashMap<String, PendingOperation>>,
}

impl Default for RuntimeCredentialCoordinator {
    fn default() -> Self {
        Self {
            group_locks: RuntimeConfigGroup::ALL
                .into_iter()
                .map(|group| (group, Arc::new(Mutex::new(()))))
                .collect(),
            operations: Mutex::new(HashMap::new()),
            pending: Mutex::new(HashMap::new()),
        }
    }
}

impl RuntimeCredentialCoordinator {
    fn group_lock(&self, group: RuntimeConfigGroup) -> Arc<Mutex<()>> {
        self.group_locks
            .get(&group)
            .expect("known runtime credential group")
            .clone()
    }

    async fn set_operation(
        &self,
        group: RuntimeConfigGroup,
        operation: RuntimeCredentialOperation,
    ) {
        self.operations.lock().await.insert(group, operation);
    }

    async fn operation(&self, group: RuntimeConfigGroup) -> RuntimeCredentialOperation {
        self.operations
            .lock()
            .await
            .get(&group)
            .cloned()
            .unwrap_or_default()
    }

    async fn group_snapshot(
        &self,
        group: RuntimeConfigGroup,
    ) -> Result<RuntimeCredentialGroupSnapshot, String> {
        let specs = runtime_config::group_specs(group);
        let items: Vec<_> = specs
            .iter()
            .map(|spec| runtime_config::status_for(spec))
            .collect();
        let required = required_fields(group);
        let configured_names: HashSet<_> = items
            .iter()
            .filter(|item| item.configured)
            .map(|item| item.name.as_str())
            .collect();
        let missing_required_fields: Vec<_> = required
            .iter()
            .filter(|name| !configured_names.contains(**name))
            .map(|name| (*name).to_string())
            .collect();
        let any_configured = items.iter().any(|item| item.configured);
        let configured = !required.is_empty() && missing_required_fields.is_empty();
        let (validation, validation_checked_at, validation_error_code) =
            matching_validation(group, &items, configured)?;
        let credential_revision = matching_revision(group, &items, configured)?;
        Ok(RuntimeCredentialGroupSnapshot {
            group,
            items,
            configured,
            partially_configured: any_configured && !configured,
            missing_required_fields,
            validation,
            validation_checked_at,
            validation_error_code,
            credential_revision,
            effect: specs
                .first()
                .expect("runtime credential group cannot be empty")
                .effect,
            operation: self.operation(group).await,
        })
    }

    async fn snapshot(&self) -> Result<RuntimeCredentialSnapshot, String> {
        let mut groups = Vec::new();
        for group in RuntimeConfigGroup::ALL {
            groups.push(self.group_snapshot(group).await?);
        }
        Ok(RuntimeCredentialSnapshot { groups })
    }

    async fn emit_group(&self, app: &AppHandle, group: RuntimeConfigGroup) {
        match self.group_snapshot(group).await {
            Ok(snapshot) => {
                if let Err(error) = app.emit(EVENT_CHANGED, snapshot) {
                    log::warn!("runtime credential event failed: {error}");
                }
            }
            Err(error) => log::warn!("runtime credential snapshot failed: {error}"),
        }
    }
}

fn required_fields(group: RuntimeConfigGroup) -> &'static [&'static str] {
    match group {
        RuntimeConfigGroup::Amap => &["AMAP_WEBSERVICE_KEY"],
        RuntimeConfigGroup::Deepseek => &["DEEPSEEK_API_KEY"],
        RuntimeConfigGroup::Kimi => &["KIMI_API_KEY"],
        RuntimeConfigGroup::Tavily => &["TAVILY_API_KEY"],
        RuntimeConfigGroup::QWeather => &["QWEATHER_API_KEY", "QWEATHER_API_HOST"],
        RuntimeConfigGroup::VolcVoice => &[
            "VOLC_ACCESS_KEY",
            "VOLC_SECRET_KEY",
            "VOLC_RTC_APP_ID",
            "VOLC_RTC_APP_KEY",
            "VOLC_SPEECH_APP_ID",
            "VOLC_SPEECH_ACCESS_TOKEN",
        ],
    }
}

fn validation_supported(group: RuntimeConfigGroup) -> bool {
    matches!(
        group,
        RuntimeConfigGroup::Deepseek | RuntimeConfigGroup::Kimi
    )
}

fn service_for(group: RuntimeConfigGroup) -> RuntimeServiceKind {
    match group {
        RuntimeConfigGroup::VolcVoice => RuntimeServiceKind::Voice,
        _ => RuntimeServiceKind::Bridge,
    }
}

fn random_id() -> String {
    let mut bytes = [0_u8; 16];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn stage_name(group: RuntimeConfigGroup, name: &str) -> String {
    format!("{STAGE_PREFIX}:{}:{name}", group.id())
}

fn stage_operation_name(group: RuntimeConfigGroup) -> String {
    format!("{STAGE_PREFIX}:{}:operation", group.id())
}

fn revision_name(group: RuntimeConfigGroup) -> String {
    format!("{REVISION_PREFIX}:{}", group.id())
}

fn reserved_value(name: &str) -> Result<Option<String>, String> {
    match runtime_config::keyring_entry(name)?.get_password() {
        Ok(value) if !value.trim().is_empty() => Ok(Some(value)),
        Ok(_) | Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(format!("runtime credential metadata read failed: {error}")),
    }
}

fn set_reserved_value(name: &str, value: &str) -> Result<(), String> {
    runtime_config::keyring_entry(name)?
        .set_password(value)
        .map_err(|error| format!("runtime credential metadata write failed: {error}"))
}

fn delete_reserved_value(name: &str) -> Result<(), String> {
    match runtime_config::keyring_entry(name)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(format!(
            "runtime credential metadata delete failed: {error}"
        )),
    }
}

fn load_state_file() -> RuntimeCredentialStateFile {
    let Some(path) = paths::runtime_credential_state_path() else {
        return RuntimeCredentialStateFile::default();
    };
    let Ok(raw) = fs::read(&path) else {
        return RuntimeCredentialStateFile::default();
    };
    match serde_json::from_slice::<RuntimeCredentialStateFile>(&raw) {
        Ok(state) if state.version == STATE_SCHEMA_VERSION => state,
        Ok(_) | Err(_) => {
            log::warn!("runtime credential state ignored because it is invalid or unsupported");
            RuntimeCredentialStateFile::default()
        }
    }
}

fn write_state_file(state: &RuntimeCredentialStateFile) -> Result<(), String> {
    let path = paths::runtime_credential_state_path()
        .ok_or_else(|| "runtime credential state directory unavailable".to_string())?;
    let parent = path
        .parent()
        .ok_or_else(|| "runtime credential state parent unavailable".to_string())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("runtime credential state directory failed: {error}"))?;
    let temporary = path.with_extension(format!("{}.tmp", random_id()));
    let bytes = serde_json::to_vec_pretty(state)
        .map_err(|error| format!("runtime credential state serialize failed: {error}"))?;
    fs::write(&temporary, bytes)
        .map_err(|error| format!("runtime credential state write failed: {error}"))?;
    replace_file(&temporary, &path)
}

fn replace_file(temporary: &Path, target: &Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    if target.exists() {
        fs::remove_file(target)
            .map_err(|error| format!("runtime credential state replace failed: {error}"))?;
    }
    fs::rename(temporary, target)
        .map_err(|error| format!("runtime credential state replace failed: {error}"))
}

fn matching_validation(
    group: RuntimeConfigGroup,
    items: &[RuntimeConfigItemStatus],
    configured: bool,
) -> Result<
    (
        RuntimeCredentialValidationStatus,
        Option<String>,
        Option<String>,
    ),
    String,
> {
    if !configured
        || items
            .iter()
            .filter(|item| required_fields(group).contains(&item.name.as_str()))
            .any(|item| item.source != Some(RuntimeConfigSource::Keyring))
    {
        return Ok((RuntimeCredentialValidationStatus::None, None, None));
    }
    let Some(revision) = reserved_value(&revision_name(group))? else {
        return Ok((RuntimeCredentialValidationStatus::None, None, None));
    };
    let state = load_state_file();
    let Some(record) = state.groups.get(group.id()) else {
        return Ok((RuntimeCredentialValidationStatus::None, None, None));
    };
    if record.revision != revision {
        return Ok((RuntimeCredentialValidationStatus::None, None, None));
    }
    Ok((
        match record.status {
            PersistedValidationStatus::Valid => RuntimeCredentialValidationStatus::Valid,
            PersistedValidationStatus::Invalid => RuntimeCredentialValidationStatus::Invalid,
        },
        Some(record.checked_at.clone()),
        Some(record.safe_code.clone()),
    ))
}

fn matching_revision(
    group: RuntimeConfigGroup,
    items: &[RuntimeConfigItemStatus],
    configured: bool,
) -> Result<Option<String>, String> {
    if !configured
        || items
            .iter()
            .filter(|item| required_fields(group).contains(&item.name.as_str()))
            .any(|item| item.source != Some(RuntimeConfigSource::Keyring))
    {
        return Ok(None);
    }
    reserved_value(&revision_name(group))
}

fn cleanup_staging(group: RuntimeConfigGroup) -> Result<(), String> {
    let mut errors = Vec::new();
    if let Err(error) = delete_reserved_value(&stage_operation_name(group)) {
        errors.push(error);
    }
    for spec in runtime_config::group_specs(group) {
        if let Err(error) = delete_reserved_value(&stage_name(group, spec.name)) {
            errors.push(error);
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

pub(crate) fn cleanup_all_staging() -> Result<(), String> {
    let mut errors = Vec::new();
    for group in RuntimeConfigGroup::ALL {
        if let Err(error) = cleanup_staging(group) {
            errors.push(error);
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}

pub(crate) fn invalidate_group_binding(group: RuntimeConfigGroup) -> Result<(), String> {
    delete_reserved_value(&revision_name(group))?;
    let mut state = load_state_file();
    state.groups.remove(group.id());
    write_state_file(&state)
}

pub(crate) fn clear_all_validation_bindings() -> Result<(), String> {
    for group in RuntimeConfigGroup::ALL {
        delete_reserved_value(&revision_name(group))?;
    }
    write_state_file(&RuntimeCredentialStateFile::default())
}

fn normalize_changes(
    payload: &ApplyRuntimeCredentialGroupPayload,
) -> Result<Vec<PendingFieldMutation>, String> {
    if payload.changes.is_empty() {
        return Err("runtime credential group has no changes".to_string());
    }
    let allowed: HashSet<_> = runtime_config::group_specs(payload.group)
        .into_iter()
        .map(|spec| spec.name)
        .collect();
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for change in &payload.changes {
        if !allowed.contains(change.name.as_str()) || !seen.insert(change.name.as_str()) {
            return Err(format!(
                "invalid or duplicate runtime credential field: {}",
                change.name
            ));
        }
        match change.action {
            RuntimeCredentialFieldAction::Set => {
                let value = change.value.as_deref().unwrap_or_default().trim();
                if value.is_empty() {
                    return Err(format!(
                        "runtime credential {} cannot be empty",
                        change.name
                    ));
                }
            }
            RuntimeCredentialFieldAction::Delete => {
                if change.value.is_some() {
                    return Err(format!(
                        "runtime credential delete cannot include a value: {}",
                        change.name
                    ));
                }
            }
        }
        result.push(PendingFieldMutation {
            name: change.name.clone(),
            action: change.action,
        });
    }
    Ok(result)
}

fn stage_candidate(
    payload: &ApplyRuntimeCredentialGroupPayload,
    operation_id: &str,
) -> Result<Vec<PendingFieldMutation>, String> {
    cleanup_staging(payload.group)?;
    let changes = normalize_changes(payload)?;
    let payload_by_name: HashMap<_, _> = payload
        .changes
        .iter()
        .map(|change| (change.name.as_str(), change))
        .collect();
    let mut candidate_names = HashSet::new();
    let stage_result = (|| -> Result<(), String> {
        for spec in runtime_config::group_specs(payload.group) {
            let candidate = match payload_by_name.get(spec.name).copied() {
                Some(change) if change.action == RuntimeCredentialFieldAction::Set => Some(
                    change
                        .value
                        .as_deref()
                        .unwrap_or_default()
                        .trim()
                        .to_string(),
                ),
                Some(_) => None,
                None => runtime_config::effective_secret_value(spec)?.map(|value| value.0),
            };
            if let Some(value) = candidate {
                candidate_names.insert(spec.name);
                set_reserved_value(&stage_name(payload.group, spec.name), &value)?;
            }
        }
        set_reserved_value(&stage_operation_name(payload.group), operation_id)
    })();
    if let Err(error) = stage_result {
        let _ = cleanup_staging(payload.group);
        return Err(error);
    }
    let missing: Vec<_> = required_fields(payload.group)
        .iter()
        .filter(|name| !candidate_names.contains(**name))
        .copied()
        .collect();
    let contains_set = changes
        .iter()
        .any(|change| change.action == RuntimeCredentialFieldAction::Set);
    if contains_set && !missing.is_empty() {
        let _ = cleanup_staging(payload.group);
        return Err(format!("invalid_candidate: missing {}", missing.join(", ")));
    }
    Ok(changes)
}

fn stage_current_group(group: RuntimeConfigGroup, operation_id: &str) -> Result<(), String> {
    cleanup_staging(group)?;
    let result = (|| -> Result<(), String> {
        let mut configured = HashSet::new();
        for spec in runtime_config::group_specs(group) {
            if let Some((value, _)) = runtime_config::effective_secret_value(spec)? {
                configured.insert(spec.name);
                set_reserved_value(&stage_name(group, spec.name), &value)?;
            }
        }
        let missing: Vec<_> = required_fields(group)
            .iter()
            .filter(|name| !configured.contains(**name))
            .copied()
            .collect();
        if !missing.is_empty() {
            return Err(format!("invalid_candidate: missing {}", missing.join(", ")));
        }
        set_reserved_value(&stage_operation_name(group), operation_id)
    })();
    if let Err(error) = result {
        let _ = cleanup_staging(group);
        return Err(error);
    }
    Ok(())
}

fn write_validation_for_revision(
    group: RuntimeConfigGroup,
    revision: &str,
    status: PersistedValidationStatus,
    safe_code: &str,
) -> Result<(), String> {
    if reserved_value(&revision_name(group))?.as_deref() != Some(revision) {
        return Ok(());
    }
    let mut state = load_state_file();
    state.groups.insert(
        group.id().to_string(),
        PersistedValidationRecord {
            revision: revision.to_string(),
            status,
            checked_at: Utc::now().to_rfc3339(),
            safe_code: safe_code.to_string(),
        },
    );
    write_state_file(&state)
}

fn read_staged_set_value(
    group: RuntimeConfigGroup,
    operation_id: &str,
    name: &str,
) -> Result<String, String> {
    if reserved_value(&stage_operation_name(group))?.as_deref() != Some(operation_id) {
        return Err("staging_operation_mismatch".to_string());
    }
    reserved_value(&stage_name(group, name))?
        .ok_or_else(|| format!("staged runtime credential missing for {name}"))
}

fn restore_managed_values(
    previous: &BTreeMap<String, Option<String>>,
    previous_revision: Option<&str>,
    group: RuntimeConfigGroup,
    previous_state: &RuntimeCredentialStateFile,
) {
    for (name, value) in previous {
        let result = match value {
            Some(value) => runtime_config::write_managed_secret(name, value),
            None => runtime_config::delete_managed_secret(name),
        };
        if let Err(error) = result {
            log::error!("runtime credential rollback failed for {name}: {error}");
        }
    }
    let result = match previous_revision {
        Some(revision) => set_reserved_value(&revision_name(group), revision),
        None => delete_reserved_value(&revision_name(group)),
    };
    if let Err(error) = result {
        log::error!("runtime credential revision rollback failed: {error}");
    }
    if let Err(error) = write_state_file(previous_state) {
        log::error!("runtime credential state rollback failed: {error}");
    }
}

fn persist_staged_operation(pending: &PendingOperation) -> Result<String, String> {
    let mut previous = BTreeMap::new();
    for change in &pending.changes {
        previous.insert(
            change.name.clone(),
            runtime_config::keyring_secret_value(&change.name)?,
        );
    }
    let previous_revision = reserved_value(&revision_name(pending.group))?;
    let previous_state = load_state_file();
    let apply = (|| -> Result<String, String> {
        for change in &pending.changes {
            match change.action {
                RuntimeCredentialFieldAction::Set => {
                    let value =
                        read_staged_set_value(pending.group, &pending.operation_id, &change.name)?;
                    runtime_config::write_managed_secret(&change.name, &value)?;
                }
                RuntimeCredentialFieldAction::Delete => {
                    runtime_config::delete_managed_secret(&change.name)?;
                }
            }
        }
        let revision = random_id();
        set_reserved_value(&revision_name(pending.group), &revision)?;
        let mut state = previous_state.clone();
        if let Some(status) = pending.validation {
            state.groups.insert(
                pending.group.id().to_string(),
                PersistedValidationRecord {
                    revision: revision.clone(),
                    status,
                    checked_at: Utc::now().to_rfc3339(),
                    safe_code: pending
                        .validation_code
                        .clone()
                        .unwrap_or_else(|| "credential_accepted".to_string()),
                },
            );
        } else {
            state.groups.remove(pending.group.id());
        }
        write_state_file(&state)?;
        Ok(revision)
    })();
    match apply {
        Ok(revision) => Ok(revision),
        Err(error) => {
            restore_managed_values(
                &previous,
                previous_revision.as_deref(),
                pending.group,
                &previous_state,
            );
            Err(error)
        }
    }
}

fn validation_response(value: &serde_json::Value) -> (&str, &str) {
    (
        value
            .get("result")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("temporarily_unavailable"),
        value
            .get("safeCode")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("validation_unavailable"),
    )
}

async fn update_and_emit(
    coordinator: &RuntimeCredentialCoordinator,
    app: &AppHandle,
    group: RuntimeConfigGroup,
    operation: RuntimeCredentialOperation,
) {
    coordinator.set_operation(group, operation).await;
    coordinator.emit_group(app, group).await;
}

async fn persist_and_apply(
    coordinator: &RuntimeCredentialCoordinator,
    supervisor: &RuntimeServiceSupervisor,
    app: &AppHandle,
    pending: PendingOperation,
) -> Result<RuntimeCredentialGroupSnapshot, String> {
    update_and_emit(
        coordinator,
        app,
        pending.group,
        RuntimeCredentialOperation {
            operation_id: Some(pending.operation_id.clone()),
            phase: RuntimeCredentialOperationPhase::Persisting,
            safe_code: None,
            message: Some("正在保存凭据".to_string()),
            persisted: false,
            applied: false,
        },
    )
    .await;
    if let Err(error) = persist_staged_operation(&pending) {
        let _ = cleanup_staging(pending.group);
        update_and_emit(
            coordinator,
            app,
            pending.group,
            RuntimeCredentialOperation {
                operation_id: Some(pending.operation_id),
                phase: RuntimeCredentialOperationPhase::Failed,
                safe_code: Some("persist_failed".to_string()),
                message: Some(error),
                persisted: false,
                applied: false,
            },
        )
        .await;
        return coordinator.group_snapshot(pending.group).await;
    }
    if let Err(error) = cleanup_staging(pending.group) {
        log::warn!("runtime credential staging cleanup deferred: {error}");
    }

    let source_overridden = pending.changes.iter().any(|change| {
        runtime_config::find_secret(&change.name)
            .map(runtime_config::status_for)
            .and_then(|item| item.source)
            == Some(RuntimeConfigSource::Env)
    });
    if source_overridden {
        update_and_emit(
            coordinator,
            app,
            pending.group,
            RuntimeCredentialOperation {
                operation_id: Some(pending.operation_id),
                phase: RuntimeCredentialOperationPhase::ApplyBlocked,
                safe_code: Some("source_override".to_string()),
                message: Some(
                    "当前生效值来自环境变量；keyring 已保存，移除环境变量覆盖后再应用。"
                        .to_string(),
                ),
                persisted: true,
                applied: false,
            },
        )
        .await;
        return coordinator.group_snapshot(pending.group).await;
    }

    update_and_emit(
        coordinator,
        app,
        pending.group,
        RuntimeCredentialOperation {
            operation_id: Some(pending.operation_id.clone()),
            phase: RuntimeCredentialOperationPhase::Restarting,
            safe_code: None,
            message: Some(match service_for(pending.group) {
                RuntimeServiceKind::Bridge => "正在重启对话服务".to_string(),
                RuntimeServiceKind::Voice => "正在重启语音服务".to_string(),
            }),
            persisted: true,
            applied: false,
        },
    )
    .await;
    let restart = supervisor
        .restart(Some(app), service_for(pending.group))
        .await;
    let operation = match restart {
        Ok(_) => RuntimeCredentialOperation {
            operation_id: Some(pending.operation_id),
            phase: RuntimeCredentialOperationPhase::Applied,
            safe_code: None,
            message: Some("凭据已保存并应用".to_string()),
            persisted: true,
            applied: true,
        },
        Err(error) if error.starts_with("service_activity_blocked") => RuntimeCredentialOperation {
            operation_id: Some(pending.operation_id),
            phase: RuntimeCredentialOperationPhase::ApplyBlocked,
            safe_code: Some("apply_blocked".to_string()),
            message: Some("凭据已保存；当前任务结束后可重试应用。".to_string()),
            persisted: true,
            applied: false,
        },
        Err(error) => RuntimeCredentialOperation {
            operation_id: Some(pending.operation_id),
            phase: RuntimeCredentialOperationPhase::Failed,
            safe_code: Some("restart_failed".to_string()),
            message: Some(error),
            persisted: true,
            applied: false,
        },
    };
    update_and_emit(coordinator, app, pending.group, operation).await;
    coordinator.group_snapshot(pending.group).await
}

#[tauri::command]
pub async fn get_runtime_credential_groups(
    state: State<'_, Arc<RuntimeCredentialCoordinator>>,
) -> Result<RuntimeCredentialSnapshot, String> {
    state.snapshot().await
}

#[tauri::command]
pub async fn apply_runtime_credential_group(
    app: AppHandle,
    payload: ApplyRuntimeCredentialGroupPayload,
    coordinator: State<'_, Arc<RuntimeCredentialCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeCredentialGroupSnapshot, String> {
    let group_lock = coordinator.group_lock(payload.group);
    let _group_guard = group_lock.lock().await;
    if coordinator
        .pending
        .lock()
        .await
        .values()
        .any(|pending| pending.group == payload.group)
    {
        return Err("credential_confirmation_pending".to_string());
    }
    let operation_id = random_id();
    update_and_emit(
        &coordinator,
        &app,
        payload.group,
        RuntimeCredentialOperation {
            operation_id: Some(operation_id.clone()),
            phase: RuntimeCredentialOperationPhase::Staging,
            safe_code: None,
            message: Some("正在准备凭据变更".to_string()),
            persisted: false,
            applied: false,
        },
    )
    .await;
    let changes = match stage_candidate(&payload, &operation_id) {
        Ok(changes) => changes,
        Err(error) => {
            update_and_emit(
                &coordinator,
                &app,
                payload.group,
                RuntimeCredentialOperation {
                    operation_id: Some(operation_id),
                    phase: RuntimeCredentialOperationPhase::Failed,
                    safe_code: Some(if error.starts_with("invalid_candidate") {
                        "invalid_candidate".to_string()
                    } else {
                        "staging_failed".to_string()
                    }),
                    message: Some(error),
                    persisted: false,
                    applied: false,
                },
            )
            .await;
            return coordinator.group_snapshot(payload.group).await;
        }
    };
    update_and_emit(
        &coordinator,
        &app,
        payload.group,
        RuntimeCredentialOperation {
            operation_id: Some(operation_id.clone()),
            phase: RuntimeCredentialOperationPhase::Validating,
            safe_code: None,
            message: Some(if validation_supported(payload.group) {
                "正在校验凭据".to_string()
            } else {
                "正在检查配置完整性".to_string()
            }),
            persisted: false,
            applied: false,
        },
    )
    .await;

    let pending = if validation_supported(payload.group) {
        match supervisor
            .validate_runtime_credential(payload.group.id(), &operation_id)
            .await
        {
            Ok(response) => {
                let (result, safe_code) = validation_response(&response);
                match result {
                    "valid" => PendingOperation {
                        operation_id,
                        group: payload.group,
                        changes,
                        validation: Some(PersistedValidationStatus::Valid),
                        validation_code: Some(safe_code.to_string()),
                    },
                    "invalid" => {
                        let _ = cleanup_staging(payload.group);
                        update_and_emit(
                            &coordinator,
                            &app,
                            payload.group,
                            RuntimeCredentialOperation {
                                operation_id: Some(operation_id),
                                phase: RuntimeCredentialOperationPhase::Failed,
                                safe_code: Some(safe_code.to_string()),
                                message: Some("凭据校验失败；原有配置未被替换。".to_string()),
                                persisted: false,
                                applied: false,
                            },
                        )
                        .await;
                        return coordinator.group_snapshot(payload.group).await;
                    }
                    _ => {
                        let pending = PendingOperation {
                            operation_id: operation_id.clone(),
                            group: payload.group,
                            changes,
                            validation: None,
                            validation_code: None,
                        };
                        coordinator
                            .pending
                            .lock()
                            .await
                            .insert(operation_id.clone(), pending);
                        update_and_emit(
                            &coordinator,
                            &app,
                            payload.group,
                            RuntimeCredentialOperation {
                                operation_id: Some(operation_id),
                                phase: RuntimeCredentialOperationPhase::AwaitingConfirmation,
                                safe_code: Some(safe_code.to_string()),
                                message: Some(
                                    "当前无法完成在线校验；可以稍后重试，或确认仍保存配置。"
                                        .to_string(),
                                ),
                                persisted: false,
                                applied: false,
                            },
                        )
                        .await;
                        return coordinator.group_snapshot(payload.group).await;
                    }
                }
            }
            Err(_) => {
                let pending = PendingOperation {
                    operation_id: operation_id.clone(),
                    group: payload.group,
                    changes,
                    validation: None,
                    validation_code: None,
                };
                coordinator
                    .pending
                    .lock()
                    .await
                    .insert(operation_id.clone(), pending);
                update_and_emit(
                    &coordinator,
                    &app,
                    payload.group,
                    RuntimeCredentialOperation {
                        operation_id: Some(operation_id),
                        phase: RuntimeCredentialOperationPhase::AwaitingConfirmation,
                        safe_code: Some("validation_unavailable".to_string()),
                        message: Some(
                            "校验服务暂时不可用；可以稍后重试，或确认仍保存配置。".to_string(),
                        ),
                        persisted: false,
                        applied: false,
                    },
                )
                .await;
                return coordinator.group_snapshot(payload.group).await;
            }
        }
    } else {
        PendingOperation {
            operation_id,
            group: payload.group,
            changes,
            validation: None,
            validation_code: None,
        }
    };
    persist_and_apply(&coordinator, &supervisor, &app, pending).await
}

#[tauri::command]
pub async fn confirm_runtime_credential_save(
    app: AppHandle,
    payload: RuntimeCredentialOperationPayload,
    coordinator: State<'_, Arc<RuntimeCredentialCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeCredentialGroupSnapshot, String> {
    let candidate = coordinator
        .pending
        .lock()
        .await
        .get(&payload.operation_id)
        .cloned()
        .ok_or_else(|| "runtime credential operation is no longer pending".to_string())?;
    let group_lock = coordinator.group_lock(candidate.group);
    let _group_guard = group_lock.lock().await;
    let pending = coordinator
        .pending
        .lock()
        .await
        .remove(&payload.operation_id)
        .ok_or_else(|| "runtime credential operation is no longer pending".to_string())?;
    persist_and_apply(&coordinator, &supervisor, &app, pending).await
}

#[tauri::command]
pub async fn cancel_runtime_credential_operation(
    app: AppHandle,
    payload: RuntimeCredentialOperationPayload,
    coordinator: State<'_, Arc<RuntimeCredentialCoordinator>>,
) -> Result<RuntimeCredentialGroupSnapshot, String> {
    let candidate = coordinator
        .pending
        .lock()
        .await
        .get(&payload.operation_id)
        .cloned()
        .ok_or_else(|| "runtime credential operation is no longer pending".to_string())?;
    let group_lock = coordinator.group_lock(candidate.group);
    let _group_guard = group_lock.lock().await;
    let pending = coordinator
        .pending
        .lock()
        .await
        .remove(&payload.operation_id)
        .ok_or_else(|| "runtime credential operation is no longer pending".to_string())?;
    cleanup_staging(pending.group)?;
    update_and_emit(
        &coordinator,
        &app,
        pending.group,
        RuntimeCredentialOperation::default(),
    )
    .await;
    coordinator.group_snapshot(pending.group).await
}

#[tauri::command]
pub async fn retry_runtime_credential_apply(
    app: AppHandle,
    payload: RetryRuntimeCredentialApplyPayload,
    coordinator: State<'_, Arc<RuntimeCredentialCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeCredentialGroupSnapshot, String> {
    let group_lock = coordinator.group_lock(payload.group);
    let _group_guard = group_lock.lock().await;
    let operation_id = random_id();
    update_and_emit(
        &coordinator,
        &app,
        payload.group,
        RuntimeCredentialOperation {
            operation_id: Some(operation_id.clone()),
            phase: RuntimeCredentialOperationPhase::Restarting,
            safe_code: None,
            message: Some("正在重新应用凭据".to_string()),
            persisted: true,
            applied: false,
        },
    )
    .await;
    let operation = match supervisor
        .restart(Some(&app), service_for(payload.group))
        .await
    {
        Ok(_) => RuntimeCredentialOperation {
            operation_id: Some(operation_id),
            phase: RuntimeCredentialOperationPhase::Applied,
            safe_code: None,
            message: Some("凭据已应用".to_string()),
            persisted: true,
            applied: true,
        },
        Err(error) if error.starts_with("service_activity_blocked") => RuntimeCredentialOperation {
            operation_id: Some(operation_id),
            phase: RuntimeCredentialOperationPhase::ApplyBlocked,
            safe_code: Some("apply_blocked".to_string()),
            message: Some("当前任务仍在运行，暂时无法重启服务。".to_string()),
            persisted: true,
            applied: false,
        },
        Err(error) => RuntimeCredentialOperation {
            operation_id: Some(operation_id),
            phase: RuntimeCredentialOperationPhase::Failed,
            safe_code: Some("restart_failed".to_string()),
            message: Some(error),
            persisted: true,
            applied: false,
        },
    };
    update_and_emit(&coordinator, &app, payload.group, operation).await;
    coordinator.group_snapshot(payload.group).await
}

#[tauri::command]
pub async fn retry_runtime_credential_validation(
    app: AppHandle,
    payload: RetryRuntimeCredentialApplyPayload,
    coordinator: State<'_, Arc<RuntimeCredentialCoordinator>>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<RuntimeCredentialGroupSnapshot, String> {
    if !validation_supported(payload.group) {
        return coordinator.group_snapshot(payload.group).await;
    }
    let group_lock = coordinator.group_lock(payload.group);
    let _group_guard = group_lock.lock().await;
    let current = coordinator.group_snapshot(payload.group).await?;
    let revision = current
        .credential_revision
        .ok_or_else(|| "credential_validation_requires_managed_keyring_source".to_string())?;
    let operation_id = random_id();
    update_and_emit(
        &coordinator,
        &app,
        payload.group,
        RuntimeCredentialOperation {
            operation_id: Some(operation_id.clone()),
            phase: RuntimeCredentialOperationPhase::Validating,
            safe_code: None,
            message: Some("正在重新校验当前凭据".to_string()),
            persisted: true,
            applied: true,
        },
    )
    .await;
    if let Err(error) = stage_current_group(payload.group, &operation_id) {
        update_and_emit(
            &coordinator,
            &app,
            payload.group,
            RuntimeCredentialOperation {
                operation_id: Some(operation_id),
                phase: RuntimeCredentialOperationPhase::Failed,
                safe_code: Some("invalid_candidate".to_string()),
                message: Some(error),
                persisted: true,
                applied: true,
            },
        )
        .await;
        return coordinator.group_snapshot(payload.group).await;
    }
    let response = supervisor
        .validate_runtime_credential(payload.group.id(), &operation_id)
        .await;
    let _ = cleanup_staging(payload.group);
    let operation = match response {
        Ok(value) => {
            let (result, safe_code) = validation_response(&value);
            match result {
                "valid" => {
                    write_validation_for_revision(
                        payload.group,
                        &revision,
                        PersistedValidationStatus::Valid,
                        safe_code,
                    )?;
                    RuntimeCredentialOperation {
                        operation_id: Some(operation_id),
                        phase: RuntimeCredentialOperationPhase::Applied,
                        safe_code: None,
                        message: Some("当前凭据校验成功".to_string()),
                        persisted: true,
                        applied: true,
                    }
                }
                "invalid" => {
                    write_validation_for_revision(
                        payload.group,
                        &revision,
                        PersistedValidationStatus::Invalid,
                        safe_code,
                    )?;
                    RuntimeCredentialOperation {
                        operation_id: Some(operation_id),
                        phase: RuntimeCredentialOperationPhase::Failed,
                        safe_code: Some(safe_code.to_string()),
                        message: Some("当前凭据无效，请修改后重新保存。".to_string()),
                        persisted: true,
                        applied: true,
                    }
                }
                _ => RuntimeCredentialOperation {
                    operation_id: Some(operation_id),
                    phase: RuntimeCredentialOperationPhase::Failed,
                    safe_code: Some(safe_code.to_string()),
                    message: Some("当前仍无法完成校验，请稍后重试。".to_string()),
                    persisted: true,
                    applied: true,
                },
            }
        }
        Err(_) => RuntimeCredentialOperation {
            operation_id: Some(operation_id),
            phase: RuntimeCredentialOperationPhase::Failed,
            safe_code: Some("validation_unavailable".to_string()),
            message: Some("校验服务暂时不可用，请稍后重试。".to_string()),
            persisted: true,
            applied: true,
        },
    };
    update_and_emit(&coordinator, &app, payload.group, operation).await;
    coordinator.group_snapshot(payload.group).await
}

#[tauri::command]
pub async fn report_runtime_credential_auth_failure(
    app: AppHandle,
    payload: RuntimeCredentialAuthFailurePayload,
    coordinator: State<'_, Arc<RuntimeCredentialCoordinator>>,
) -> Result<RuntimeCredentialGroupSnapshot, String> {
    let group_lock = coordinator.group_lock(payload.group);
    let _group_guard = group_lock.lock().await;
    let current = coordinator.group_snapshot(payload.group).await?;
    if current.credential_revision.as_deref() != Some(payload.credential_revision.as_str()) {
        return Ok(current);
    }
    write_validation_for_revision(
        payload.group,
        &payload.credential_revision,
        PersistedValidationStatus::Invalid,
        "runtime_auth_failed",
    )?;
    update_and_emit(
        &coordinator,
        &app,
        payload.group,
        RuntimeCredentialOperation {
            operation_id: None,
            phase: RuntimeCredentialOperationPhase::Failed,
            safe_code: Some("runtime_auth_failed".to_string()),
            message: Some("运行时确认当前凭据无效，请重新配置。".to_string()),
            persisted: true,
            applied: true,
        },
    )
    .await;
    coordinator.group_snapshot(payload.group).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn required_fields_keep_voice_optional_api_key_out_of_completeness() {
        assert!(!required_fields(RuntimeConfigGroup::VolcVoice).contains(&"VOLC_SPEECH_API_KEY"));
        assert!(
            required_fields(RuntimeConfigGroup::VolcVoice).contains(&"VOLC_SPEECH_ACCESS_TOKEN")
        );
    }

    #[test]
    fn reserved_names_do_not_contain_secret_material() {
        assert_eq!(
            stage_operation_name(RuntimeConfigGroup::Deepseek),
            "__agent_friend_runtime_credential_stage__:deepseek:operation"
        );
        assert_eq!(
            revision_name(RuntimeConfigGroup::Kimi),
            "__agent_friend_runtime_credential_revision__:kimi"
        );
    }

    #[test]
    fn state_file_never_serializes_secret_fields() {
        let mut state = RuntimeCredentialStateFile::default();
        state.groups.insert(
            "deepseek".to_string(),
            PersistedValidationRecord {
                revision: "random-revision".to_string(),
                status: PersistedValidationStatus::Valid,
                checked_at: "2026-08-20T00:00:00Z".to_string(),
                safe_code: "credential_accepted".to_string(),
            },
        );
        let value = serde_json::to_value(state).expect("serialize state");
        let text = value.to_string();
        assert!(!text.contains("apiKey"));
        assert!(!text.contains("secret"));
        assert!(text.contains("random-revision"));
    }
}
