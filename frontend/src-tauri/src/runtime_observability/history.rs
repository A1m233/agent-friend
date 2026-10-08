//! History navigation spans, separate from global startup milestones.
use super::RuntimeObserver;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::sync::Arc;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct HistoryObservation {
    navigation_id: String,
    session_id: String,
    kind: String,
    phase: String,
    elapsed_ms: f64,
    duration_ms: Option<f64>,
    event_count: Option<u64>,
    message_count: Option<u64>,
    response_bytes: Option<u64>,
    request_id: Option<String>,
    status: Option<String>,
}

fn identity(value: &str, maximum: usize) -> bool {
    !value.is_empty()
        && value.len() <= maximum
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
}

impl HistoryObservation {
    fn details(self) -> Result<Value, String> {
        if !identity(&self.navigation_id, 64)
            || !identity(&self.session_id, 256)
            || self
                .request_id
                .as_deref()
                .is_some_and(|id| !identity(id, 64))
            || !matches!(
                self.kind.as_str(),
                "latest" | "before" | "after" | "around" | "content"
            )
            || !matches!(
                self.phase.as_str(),
                "started"
                    | "service-wait"
                    | "service-ready"
                    | "response-received"
                    | "decode-finished"
                    | "projection-ready"
                    | "state-published"
                    | "react-committed"
                    | "frame-ready"
                    | "finished"
            )
            || !self.elapsed_ms.is_finite()
            || self.elapsed_ms < 0.0
            || self
                .duration_ms
                .is_some_and(|value| !value.is_finite() || value < 0.0)
            || self
                .status
                .as_deref()
                .is_some_and(|status| !matches!(status, "success" | "error" | "cancelled"))
        {
            return Err("history_observation_invalid".into());
        }
        let session_key = format!("{:x}", Sha256::digest(self.session_id.as_bytes()));
        Ok(json!({
            "navigationId": self.navigation_id, "sessionKey": &session_key[..16],
            "kind": self.kind, "phase": self.phase, "elapsedMs": self.elapsed_ms,
            "durationMs": self.duration_ms, "eventCount": self.event_count,
            "messageCount": self.message_count, "responseBytes": self.response_bytes,
            "requestId": self.request_id, "status": self.status,
            "clock": "navigation-monotonic", "windowLabel": "chat",
        }))
    }
}

#[tauri::command]
pub(crate) fn record_history_observation(
    window: tauri::WebviewWindow,
    observer: tauri::State<'_, Arc<RuntimeObserver>>,
    observation: HistoryObservation,
) -> Result<(), String> {
    if window.label() != "chat" {
        return Err("history_observation_wrong_window".into());
    }
    observer.record("history", "navigation-phase", observation.details()?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> HistoryObservation {
        serde_json::from_value(json!({
            "navigationId": "00000000-0000-0000-0000-000000000000",
            "sessionId": "private-session", "kind": "latest", "phase": "frame-ready",
            "elapsedMs": 12.0, "messageCount": 3, "requestId": "a123",
        }))
        .unwrap()
    }

    #[test]
    fn history_evidence_hashes_session_and_validates_fields() {
        let details = sample().details().unwrap();
        assert_eq!(details["phase"], "frame-ready");
        assert_eq!(details["sessionKey"].as_str().unwrap().len(), 16);
        assert!(!details.to_string().contains("private-session"));
        let mut input = sample();
        input.phase = "private content".into();
        assert!(input.details().is_err());
        let mut input = sample();
        input.elapsed_ms = f64::NAN;
        assert!(input.details().is_err());
        let mut input = sample();
        input.request_id = Some("/private/path".into());
        assert!(input.details().is_err());
    }
}
