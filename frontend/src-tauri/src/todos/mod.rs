//! Desktop-only Todo mutations. The owner credential never enters the WebView.
use crate::runtime_services::RuntimeServiceSupervisor;
use std::sync::Arc;
use tauri::{State, WebviewWindow};

fn validate_request(window: &str, body: &serde_json::Value) -> Result<(), String> {
    if window != "chat" {
        return Err("todo_window_not_allowed".into());
    }
    match body.get("action").and_then(|value| value.as_str()) {
        Some(
            "create" | "update" | "complete" | "cancel" | "reopen" | "add_reminder"
            | "update_reminder" | "remove_reminder",
        ) => Ok(()),
        _ => Err("todo_action_not_allowed".into()),
    }
}

#[tauri::command]
pub async fn mutate_todo(
    window: WebviewWindow,
    state: State<'_, Arc<RuntimeServiceSupervisor>>,
    request: serde_json::Value,
) -> Result<serde_json::Value, String> {
    validate_request(window.label(), &request)?;
    state.todo_operation(request).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_chat_can_mutate_and_actions_cannot_choose_a_control_endpoint() {
        for action in [
            "create",
            "update",
            "complete",
            "cancel",
            "reopen",
            "add_reminder",
            "update_reminder",
            "remove_reminder",
        ] {
            let body = serde_json::json!({"action": action});
            assert!(validate_request("chat", &body).is_ok());
            assert!(validate_request("pet", &body).is_err());
        }
        assert!(validate_request("chat", &serde_json::json!({"action": "../shutdown"})).is_err());
        assert!(validate_request("chat", &serde_json::json!({})).is_err());
    }
}
