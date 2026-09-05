//! Input adapters only: observations must never become a second input controller.
use super::{model, PetInputEvidence};
use serde::Serialize;
use serde_json::{json, Map, Value};
use std::sync::Mutex;
use tauri::{Manager, WebviewWindow};

pub(crate) fn runtime_identity(
    build: Option<&crate::install_contract::BuildIdentity>,
    version: &str,
) -> Value {
    let mut identity = json!({"buildMode": if cfg!(debug_assertions) {"debug"} else {"release"},
        "platform":std::env::consts::OS,"appVersion":version});
    if let Some(build) = build {
        identity["buildId"] = json!(build.build_id);
        identity["sourceCommit"] = json!(build.source_commit);
        identity["sourceDirty"] = json!(build.dirty);
    }
    if let Ok(version) = tauri::webview_version() {
        identity["webviewVersion"] = json!(version);
    }
    identity
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct DprSnapshot {
    pub(crate) value: f64,
    pub(crate) revision: u64,
}

#[derive(Default)]
pub(crate) struct PetWebviewState {
    dpr: Mutex<DprSnapshot>,
}

impl PetWebviewState {
    pub(crate) fn snapshot(&self) -> Option<DprSnapshot> {
        self.dpr.lock().ok().map(|value| *value)
    }

    /// Capture the result under the original business lock, enqueue only after unlocking.
    pub(crate) fn apply(&self, dpr: f64) -> Option<(DprSnapshot, DprSnapshot, bool)> {
        let mut current = self.dpr.lock().ok()?;
        let before = *current;
        let changed = (before.value - dpr).abs() >= f64::EPSILON || (before.value - dpr).is_nan();
        // Preserve the previous early-return condition, including its non-finite behavior.
        if changed {
            current.value = dpr;
            current.revision = current.revision.saturating_add(1);
        }
        Some((before, *current, changed))
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CursorDiagnostic {
    pub(crate) sample_id: u64,
    pub(crate) dpr_revision: u64,
    pub(crate) scale: f64,
    pub(crate) scale_source: &'static str,
    pub(crate) physical_x: f64,
    pub(crate) physical_y: f64,
    pub(crate) origin_x: i32,
    pub(crate) origin_y: i32,
}

pub(crate) fn resolve_scale(
    snapshot: Option<DprSnapshot>,
    fallback: f64,
) -> (f64, u64, &'static str) {
    match snapshot {
        Some(value) if value.value > 0.0 => (value.value, value.revision, "webview"),
        value => (fallback, value.map_or(0, |v| v.revision), "fallback"),
    }
}

/// Optional metadata is untrusted; reject it independently from the native fact/result.
pub(crate) fn metadata(value: Option<&Value>) -> Result<Map<String, Value>, ()> {
    let Some(value) = value.filter(|v| !v.is_null()) else {
        return Ok(Map::new());
    };
    let Some(fields) = value.as_object() else {
        return Err(());
    };
    if fields.len() > 10
        || fields.values().any(|value| match value {
            Value::String(text) => text.len() > 96,
            Value::Number(_) => false,
            _ => true,
        })
        || fields.keys().any(|key| {
            !matches!(
                key.as_str(),
                "pageInstanceId"
                    | "frontendSeq"
                    | "requestId"
                    | "monotonicMs"
                    | "displayRevision"
                    | "sampleId"
                    | "dprRevision"
                    | "actualDpr"
                    | "displayDpr"
            )
        })
    {
        return Err(());
    }
    let mut candidate = fields.clone();
    candidate.insert("source".into(), json!("native"));
    candidate.insert("kind".into(), json!("coverage-gap"));
    if !model::validate_event(&Value::Object(candidate)) {
        return Err(());
    }
    Ok(fields.clone())
}

pub(crate) fn record_native(
    manager: Option<&PetInputEvidence>,
    fact: Value,
    diagnostic: Option<&Value>,
) {
    let Some(manager) = manager else { return };
    let mut fields = match metadata(diagnostic) {
        Ok(fields) => fields,
        Err(()) => {
            manager.note_invalid();
            Map::new()
        }
    };
    // Native fields win even over well-formed metadata, e.g. its observed DPR revision.
    if let Some(fact) = fact.as_object() {
        fields.extend(fact.clone());
    }
    fields.insert("source".into(), json!("native"));
    manager.record(Value::Object(fields));
}

pub(crate) fn record_dpr(
    manager: Option<&PetInputEvidence>,
    state: &PetWebviewState,
    dpr: f64,
    diagnostic: Option<&Value>,
) {
    record_native(
        manager,
        json!({"kind":"dpr-report","reportedDpr":dpr,"stage":"request"}),
        diagnostic,
    );
    let fact = match state.apply(dpr) {
        Some((before, after, changed)) => {
            if changed {
                log::info!("set_pet_webview_dpr: {dpr}");
            }
            json!({"kind":"dpr-applied", "reportedDpr":dpr, "oldDpr":before.value,
                "newDpr":after.value, "dprRevision":after.revision,
                "outcome":if changed {"applied"} else {"unchanged"}})
        }
        None => json!({"kind":"dpr-applied","outcome":"not-applied","errorKind":"lock-failed"}),
    };
    record_native(manager, fact, diagnostic);
}

#[tauri::command]
pub(crate) fn record_pet_input_evidence(
    window: WebviewWindow,
    events: Value,
) -> Result<bool, String> {
    if window.label() != "pet" {
        return Err("pet input evidence: window is not allowed".into());
    }
    window
        .try_state::<PetInputEvidence>()
        .map(|manager| manager.record_batch(events))
        .ok_or_else(|| "pet input evidence: collector unavailable".to_string())
}

pub(crate) fn record_window_event<R: tauri::Runtime>(
    window: &tauri::Window<R>,
    event: &tauri::WindowEvent,
) {
    if window.label() != "pet" {
        return;
    }
    let fact = match event {
        tauri::WindowEvent::Moved(pos) => {
            json!({"kind":"window-observed","windowOrigin":{"x":pos.x,"y":pos.y}})
        }
        tauri::WindowEvent::Resized(size) => {
            json!({"kind":"window-observed","windowSize":{"width":size.width,"height":size.height}})
        }
        tauri::WindowEvent::ScaleFactorChanged {
            scale_factor,
            new_inner_size,
            ..
        } => json!({"kind":"window-observed",
            "windowScaleFactor":scale_factor,"windowSize":{"width":new_inner_size.width,"height":new_inner_size.height}}),
        _ => return,
    };
    let manager = window.try_state::<PetInputEvidence>();
    record_native(manager.as_deref(), fact, None);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dpr_last_arrival_and_equal_value_behavior_are_unchanged() {
        let state = PetWebviewState::default();
        let (_, first, changed) = state.apply(1.25).unwrap();
        assert!(changed);
        assert_eq!(first.revision, 1);
        let (_, equal, changed) = state.apply(1.25).unwrap();
        assert!(!changed);
        assert_eq!(equal.revision, 1);
        let (_, last, changed) = state.apply(1.75).unwrap();
        assert!(changed);
        assert_eq!(last.value, 1.75);
        assert_eq!(last.revision, 2);
        assert_eq!(resolve_scale(state.snapshot(), 2.0), (1.75, 2, "webview"));
        state.apply(0.0);
        assert_eq!(resolve_scale(state.snapshot(), 2.0), (2.0, 3, "fallback"));
    }

    #[test]
    fn metadata_cannot_override_native_fields_or_carry_content() {
        assert!(metadata(None).unwrap().is_empty());
        assert!(metadata(Some(&Value::Null)).unwrap().is_empty());
        assert!(metadata(Some(&json!({"requestId":3,"pageInstanceId":"abcd-1234"}))).is_ok());
        for value in [
            json!("private"),
            json!({"kind":"dpr-applied"}),
            json!({"source":"native"}),
            json!({"pageInstanceId":"C:/private"}),
            json!({"actualDpr":"secret"}),
            json!({"styleActual":1}),
            json!({"requestId":"private-token"}),
            json!({"pageInstanceId":"a".repeat(4096)}),
            json!({"requestId":{"nested":"private"}}),
        ] {
            assert!(metadata(Some(&value)).is_err());
        }
    }

    #[test]
    fn missing_snapshot_and_invalid_business_scale_keep_original_fallback() {
        assert_eq!(resolve_scale(None, 1.75), (1.75, 0, "fallback"));
        let state = PetWebviewState::default();
        state.apply(-1.0);
        assert_eq!(resolve_scale(state.snapshot(), 1.25), (1.25, 1, "fallback"));
        state.apply(f64::NAN);
        assert_eq!(resolve_scale(state.snapshot(), 1.25), (1.25, 2, "fallback"));
    }
}
