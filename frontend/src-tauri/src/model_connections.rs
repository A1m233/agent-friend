//! Versioned credentials are prepared before publishing a complete settings snapshot.
use crate::{
    runtime_config,
    runtime_services::RuntimeServiceSupervisor,
    settings::{self, ModelSelection, Settings},
};
use rand::{rngs::OsRng, RngCore};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};
use tauri::{AppHandle, State};
use tauri_plugin_store::StoreExt;

const PENDING_CREDENTIALS: &str = "modelConnectionPendingCredentials";

pub(crate) fn cleanup_pending_credentials(
    app: &AppHandle,
    state: &Arc<Mutex<Settings>>,
) -> Result<(), String> {
    let current = settings::migration_snapshot(state)?;
    let store = app
        .store(crate::paths::SETTINGS_STORE_PATH)
        .map_err(|_| "credential_journal_unavailable")?;
    let mut remaining = Vec::new();
    if let Some(Value::Array(names)) = store.get(PENDING_CREDENTIALS) {
        for value in names {
            let Some(name) = value.as_str() else {
                return Err("invalid_credential_journal".into());
            };
            if !name.starts_with("CUSTOM_LLM_") {
                return Err("invalid_credential_journal".into());
            }
            let referenced = current.model_connections["connections"]
                .as_array()
                .into_iter()
                .flatten()
                .any(|item| secret_name(item).ok().as_deref() == Some(name));
            if !referenced && runtime_config::delete_managed_secret(name).is_err() {
                remaining.push(value);
            }
        }
    }
    store.set(PENDING_CREDENTIALS, json!(remaining));
    store.save().map_err(|_| "credential_journal_save_failed")?;
    if remaining.is_empty() {
        Ok(())
    } else {
        Err("credential_cleanup_pending".into())
    }
}

#[derive(Default)]
pub struct ModelConnectionCoordinator {
    operation: tokio::sync::Mutex<Option<String>>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveConnections {
    expected_revision: u64,
    connections: Vec<Value>,
    api_keys: BTreeMap<String, String>,
    chat_model: ModelSelection,
    memory_model: ModelSelection,
}

fn secret_name(connection: &Value) -> Result<String, String> {
    let id = connection["id"].as_str().ok_or("invalid_connection")?;
    let revision = connection["credentialRevision"]
        .as_str()
        .ok_or("invalid_connection")?;
    Ok(format!("CUSTOM_LLM_{id}_{revision}_API_KEY"))
}

#[tauri::command]
pub async fn save_model_connections(
    app: AppHandle,
    settings: State<'_, Arc<Mutex<Settings>>>,
    coordinator: State<'_, ModelConnectionCoordinator>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
    payload: SaveConnections,
) -> Result<Value, String> {
    let mut operation = coordinator.operation.lock().await;
    if operation.is_some() {
        return Err("connection_apply_pending".into());
    }
    cleanup_pending_credentials(&app, &settings)?;
    let previous = settings::migration_snapshot(&settings)?;
    if previous.model_connections["revision"].as_u64() != Some(payload.expected_revision) {
        return Err("settings_changed_reload_required".into());
    }
    let next = json!({"revision": payload.expected_revision.checked_add(1).ok_or("revision_overflow")?, "connections":payload.connections});
    let mut bytes = [0_u8; 16];
    OsRng.fill_bytes(&mut bytes);
    let token: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    *operation = Some(token.clone());
    if let Err(error) = supervisor.model_connection_operation("prepare", json!({"operationId":token,"modelConnections":next,"chatModel":payload.chat_model,"memoryModel":payload.memory_model})).await {
        if !matches!(error.as_str(), "model_connection_request_failed" | "model_connection_invalid_response") { *operation = None; }
        return Err(error);
    }
    let mut staged = Vec::new();
    let persist = (|| -> Result<(), String> {
        let mut journal: Vec<String> = previous.model_connections["connections"]
            .as_array()
            .into_iter()
            .flatten()
            .map(secret_name)
            .collect::<Result<_, _>>()?;
        journal.extend(
            payload
                .connections
                .iter()
                .map(secret_name)
                .collect::<Result<Vec<_>, _>>()?,
        );
        let store = app
            .store(crate::paths::SETTINGS_STORE_PATH)
            .map_err(|_| "credential_journal_unavailable")?;
        store.set(PENDING_CREDENTIALS, json!(journal));
        store.save().map_err(|_| "credential_journal_save_failed")?;
        for connection in &payload.connections {
            let name = secret_name(connection)?;
            let id = connection["id"].as_str().ok_or("invalid_connection")?;
            if let Some(key) = payload.api_keys.get(id) {
                if key.trim().is_empty() {
                    return Err("api_key_required".into());
                }
                if previous.model_connections["connections"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .any(|old| secret_name(old).ok().as_ref() == Some(&name))
                {
                    return Err("credential_revision_must_change".into());
                }
                staged.push(name.clone());
                runtime_config::write_managed_secret(&name, key.trim())?;
            } else if runtime_config::keyring_secret_value(&name)?.is_none() {
                return Err("api_key_required".into());
            }
        }
        settings::save_connection_settings(
            &app,
            &settings,
            &previous,
            next.clone(),
            payload.chat_model,
            payload.memory_model,
        )?;
        Ok(())
    })();
    if persist.is_err() {
        for name in staged {
            let _ = runtime_config::delete_managed_secret(&name);
        }
    }
    let applied = supervisor
        .model_connection_operation("finish", json!({"operationId": token}))
        .await;
    if applied.is_ok() {
        *operation = None;
    }
    persist?;
    let cleanup_complete = cleanup_pending_credentials(&app, &settings).is_ok();
    Ok(json!({"persisted": true, "applied": applied.is_ok(), "cleanupComplete": cleanup_complete}))
}

#[tauri::command]
pub async fn retry_model_connections(
    app: AppHandle,
    settings: State<'_, Arc<Mutex<Settings>>>,
    coordinator: State<'_, ModelConnectionCoordinator>,
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
) -> Result<(), String> {
    let mut operation = coordinator.operation.lock().await;
    if let Some(token) = operation.as_ref() {
        supervisor
            .model_connection_operation("finish", json!({"operationId":token}))
            .await?;
        *operation = None;
    }
    cleanup_pending_credentials(&app, &settings)
}

/// Draft secrets travel only over the owner-authenticated loopback request.
/// They are never written to settings, keyring, or diagnostic output here.
#[tauri::command]
pub async fn inspect_model_connection(
    supervisor: State<'_, Arc<RuntimeServiceSupervisor>>,
    payload: Value,
) -> Result<Value, String> {
    supervisor
        .model_connection_operation("inspect", payload)
        .await
}
