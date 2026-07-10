use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Runtime};
use tauri_plugin_store::StoreExt;

use crate::paths;

const CHAT_UI_KEY: &str = "chatUi";
const PET_UI_KEY: &str = "petUi";

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

#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PetUiPersistence {
    pub anchor: Option<PetMonitorRelativeAnchorPersistence>,
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
    let state = PetUiPersistence {
        anchor: payload.anchor,
    };
    store.set(
        PET_UI_KEY,
        serde_json::to_value(state).map_err(|e| e.to_string())?,
    );
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
                })
            }
        );
    }
}
