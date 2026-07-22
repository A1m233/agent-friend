use serde::{Deserialize, Deserializer, Serialize};
use tauri::{AppHandle, Runtime};
use tauri_plugin_store::StoreExt;

use crate::paths;

const CHAT_UI_KEY: &str = "chatUi";
const PET_UI_KEY: &str = "petUi";
const MIGRATION_RESULT_KEY: &str = "migrationResult";

#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ChatUiPersistence {
    pub last_chat_session_id: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PetPointPersistence {
    pub x: f64,
    pub y: f64,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PetMonitorSnapshotPersistence {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    #[serde(default)]
    pub scale_milli: Option<i32>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PetMonitorRelativeAnchorPersistence {
    pub monitor: PetMonitorSnapshotPersistence,
    pub relative_anchor: PetPointPersistence,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, Default, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum PetFacingPersistence {
    Left,
    #[default]
    Right,
}

fn deserialize_pet_facing<'de, D>(deserializer: D) -> Result<PetFacingPersistence, D::Error>
where
    D: Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    Ok(match value.as_str() {
        Some("left") => PetFacingPersistence::Left,
        _ => PetFacingPersistence::Right,
    })
}

#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PetUiPersistence {
    pub anchor: Option<PetMonitorRelativeAnchorPersistence>,
    #[serde(default, deserialize_with = "deserialize_pet_facing")]
    pub facing: PetFacingPersistence,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetLastChatSessionPayload {
    pub session_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetPetMonitorAnchorPayload {
    pub anchor: Option<PetMonitorRelativeAnchorPersistence>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetPetFacingPayload {
    pub facing: PetFacingPersistence,
}

fn normalize_session_id(value: Option<String>) -> Option<String> {
    value
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

fn parse_chat_ui(value: Option<serde_json::Value>) -> Result<ChatUiPersistence, String> {
    let Some(value) = value else {
        return Ok(ChatUiPersistence::default());
    };

    let mut state: ChatUiPersistence =
        serde_json::from_value(value).map_err(|e| format!("ui-state: parse chatUi failed: {e}"))?;
    state.last_chat_session_id = normalize_session_id(state.last_chat_session_id);
    Ok(state)
}

fn parse_pet_ui(value: Option<serde_json::Value>) -> Result<PetUiPersistence, String> {
    let Some(value) = value else {
        return Ok(PetUiPersistence::default());
    };

    serde_json::from_value(value).map_err(|e| format!("ui-state: parse petUi failed: {e}"))
}

fn update_pet_monitor_anchor(
    mut state: PetUiPersistence,
    anchor: Option<PetMonitorRelativeAnchorPersistence>,
) -> PetUiPersistence {
    state.anchor = anchor;
    state
}

fn update_pet_facing(
    mut state: PetUiPersistence,
    facing: PetFacingPersistence,
) -> PetUiPersistence {
    state.facing = facing;
    state
}

#[tauri::command]
pub fn get_chat_ui_persistence<R: Runtime>(app: AppHandle<R>) -> Result<ChatUiPersistence, String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    parse_chat_ui(store.get(CHAT_UI_KEY))
}

#[tauri::command]
pub fn set_last_chat_session_id<R: Runtime>(
    app: AppHandle<R>,
    payload: SetLastChatSessionPayload,
) -> Result<(), String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    let state = ChatUiPersistence {
        last_chat_session_id: normalize_session_id(payload.session_id),
    };
    store.set(
        CHAT_UI_KEY,
        serde_json::to_value(state).map_err(|e| e.to_string())?,
    );
    store.save().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_pet_ui_persistence<R: Runtime>(app: AppHandle<R>) -> Result<PetUiPersistence, String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    parse_pet_ui(store.get(PET_UI_KEY))
}

#[tauri::command]
pub fn set_pet_monitor_anchor<R: Runtime>(
    app: AppHandle<R>,
    payload: SetPetMonitorAnchorPayload,
) -> Result<(), String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    let state = update_pet_monitor_anchor(parse_pet_ui(store.get(PET_UI_KEY))?, payload.anchor);
    store.set(
        PET_UI_KEY,
        serde_json::to_value(state).map_err(|e| e.to_string())?,
    );
    store.save().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn set_pet_facing<R: Runtime>(
    app: AppHandle<R>,
    payload: SetPetFacingPayload,
) -> Result<(), String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    let state = update_pet_facing(parse_pet_ui(store.get(PET_UI_KEY))?, payload.facing);
    store.set(
        PET_UI_KEY,
        serde_json::to_value(state).map_err(|e| e.to_string())?,
    );
    store.save().map_err(|e| e.to_string())
}

pub fn migration_snapshot<R: Runtime>(app: &AppHandle<R>) -> Result<ChatUiPersistence, String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    parse_chat_ui(store.get(CHAT_UI_KEY))
}

pub fn migration_replace<R: Runtime>(
    app: &AppHandle<R>,
    mut state: ChatUiPersistence,
) -> Result<(), String> {
    if let Some(session_id) = state.last_chat_session_id.as_deref() {
        let exists = std::fs::read_dir(paths::user_data_dir().join("sessions"))
            .ok()
            .into_iter()
            .flatten()
            .filter_map(Result::ok)
            .any(|entry| {
                entry.path().extension().and_then(|value| value.to_str()) == Some("jsonl")
                    && entry.path().file_stem().and_then(|value| value.to_str()) == Some(session_id)
            });
        if !exists {
            state.last_chat_session_id = None;
        }
    }
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    store.set(
        CHAT_UI_KEY,
        serde_json::to_value(state).map_err(|e| e.to_string())?,
    );
    store.save().map_err(|e| e.to_string())
}

pub fn migration_result<R: Runtime>(
    app: &AppHandle<R>,
) -> Result<Option<serde_json::Value>, String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    Ok(store.get(MIGRATION_RESULT_KEY))
}

pub fn set_migration_result<R: Runtime>(
    app: &AppHandle<R>,
    result: serde_json::Value,
) -> Result<(), String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    store.set(MIGRATION_RESULT_KEY, result);
    store.save().map_err(|e| e.to_string())
}

pub fn clear_migration_result<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    store.delete(MIGRATION_RESULT_KEY);
    store.save().map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_session_id_trims_and_drops_empty_values() {
        assert_eq!(
            normalize_session_id(Some(" sid ".to_string())),
            Some("sid".to_string())
        );
        assert_eq!(normalize_session_id(Some("   ".to_string())), None);
        assert_eq!(normalize_session_id(None), None);
    }

    #[test]
    fn parse_chat_ui_returns_default_when_missing() {
        assert_eq!(
            parse_chat_ui(None).expect("parse"),
            ChatUiPersistence::default()
        );
    }

    #[test]
    fn parse_chat_ui_normalizes_session_id() {
        let value = serde_json::json!({ "lastChatSessionId": " sid-1 " });

        assert_eq!(
            parse_chat_ui(Some(value)).expect("parse"),
            ChatUiPersistence {
                last_chat_session_id: Some("sid-1".to_string())
            }
        );
    }

    #[test]
    fn parse_pet_ui_returns_default_when_missing() {
        assert_eq!(
            parse_pet_ui(None).expect("parse"),
            PetUiPersistence::default()
        );
    }

    #[test]
    fn parse_pet_ui_preserves_monitor_relative_anchor() {
        let value = serde_json::json!({
            "anchor": {
                "monitor": {
                    "x": -2560,
                    "y": 0,
                    "width": 2560,
                    "height": 1440,
                    "scaleMilli": 1250
                },
                "relativeAnchor": {
                    "x": 0.5,
                    "y": 0.25
                }
            }
        });

        assert_eq!(
            parse_pet_ui(Some(value)).expect("parse"),
            PetUiPersistence {
                anchor: Some(PetMonitorRelativeAnchorPersistence {
                    monitor: PetMonitorSnapshotPersistence {
                        x: -2560,
                        y: 0,
                        width: 2560,
                        height: 1440,
                        scale_milli: Some(1250),
                    },
                    relative_anchor: PetPointPersistence { x: 0.5, y: 0.25 },
                }),
                facing: PetFacingPersistence::Right,
            }
        );
    }

    #[test]
    fn parse_pet_ui_preserves_facing_and_defaults_invalid_values() {
        assert_eq!(
            parse_pet_ui(Some(serde_json::json!({ "facing": "left" })))
                .expect("parse")
                .facing,
            PetFacingPersistence::Left
        );
        assert_eq!(
            parse_pet_ui(Some(serde_json::json!({ "facing": "right" })))
                .expect("parse")
                .facing,
            PetFacingPersistence::Right
        );

        for invalid_facing in [
            serde_json::json!("sideways"),
            serde_json::json!(42),
            serde_json::json!({ "unexpected": true }),
            serde_json::Value::Null,
        ] {
            assert_eq!(
                parse_pet_ui(Some(serde_json::json!({
                    "anchor": {
                        "monitor": { "x": 0, "y": 0, "width": 100, "height": 100 },
                        "relativeAnchor": { "x": 0.25, "y": 0.75 }
                    },
                    "facing": invalid_facing
                })))
                .expect("parse"),
                PetUiPersistence {
                    anchor: Some(PetMonitorRelativeAnchorPersistence {
                        monitor: PetMonitorSnapshotPersistence {
                            x: 0,
                            y: 0,
                            width: 100,
                            height: 100,
                            scale_milli: None,
                        },
                        relative_anchor: PetPointPersistence { x: 0.25, y: 0.75 },
                    }),
                    facing: PetFacingPersistence::Right,
                }
            );
        }
    }

    #[test]
    fn pet_ui_field_updates_preserve_the_other_field() {
        let anchor = PetMonitorRelativeAnchorPersistence {
            monitor: PetMonitorSnapshotPersistence {
                x: 1,
                y: 2,
                width: 100,
                height: 200,
                scale_milli: Some(1250),
            },
            relative_anchor: PetPointPersistence { x: 0.2, y: 0.8 },
        };
        let state = update_pet_facing(
            PetUiPersistence {
                anchor: Some(anchor.clone()),
                facing: PetFacingPersistence::Right,
            },
            PetFacingPersistence::Left,
        );
        assert_eq!(state.anchor, Some(anchor));
        assert_eq!(state.facing, PetFacingPersistence::Left);

        let state = update_pet_monitor_anchor(state, None);
        assert_eq!(state.anchor, None);
        assert_eq!(state.facing, PetFacingPersistence::Left);
    }
}
