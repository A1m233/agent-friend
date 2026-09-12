//! Input adapters only: observations must never become a second input controller.
use super::{model, PetInputEvidence};
use serde::{Deserialize, Serialize};
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

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, PartialOrd)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DprVersion {
    page_started_at: f64,
    sequence: u64,
}

#[derive(Default)]
pub(crate) struct PetWebviewState {
    dpr: Mutex<(DprSnapshot, Option<DprVersion>)>,
}

impl PetWebviewState {
    pub(crate) fn snapshot(&self) -> Option<DprSnapshot> {
        self.dpr.lock().ok().map(|value| value.0)
    }

    /// Capture the result under the original business lock, enqueue only after unlocking.
    pub(crate) fn apply(
        &self,
        dpr: f64,
        version: DprVersion,
    ) -> Option<(DprSnapshot, DprSnapshot, bool)> {
        let mut current = self.dpr.lock().ok()?;
        let before = current.0;
        if !dpr.is_finite()
            || dpr <= 0.0
            || !version.page_started_at.is_finite()
            || version.page_started_at <= 0.0
            || version.sequence == 0
            || current.1.is_some_and(|previous| version <= previous)
        {
            return Some((before, before, false));
        }
        // Equal DPR must still advance ordering, rejecting delayed intermediate values.
        current.1 = Some(version);
        let changed = (before.value - dpr).abs() >= f64::EPSILON || (before.value - dpr).is_nan();
        // Value and ordering change under the same cursor-feed lock.
        if changed {
            current.0.value = dpr;
            current.0.revision = current.0.revision.saturating_add(1);
        }
        Some((before, current.0, changed))
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
    version: DprVersion,
    diagnostic: Option<&Value>,
) {
    record_native(
        manager,
        json!({"kind":"dpr-report","reportedDpr":dpr,"stage":"request"}),
        diagnostic,
    );
    let fact = match state.apply(dpr, version) {
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

    fn version(page: f64, sequence: u64) -> DprVersion {
        DprVersion {
            page_started_at: page,
            sequence,
        }
    }

    #[test]
    fn delayed_middle_report_cannot_overwrite_equal_latest_value() {
        let state = PetWebviewState::default();
        state.apply(1.25, version(100.0, 2));
        state.apply(1.25, version(100.0, 4));
        state.apply(1.75, version(100.0, 3));
        assert_eq!(resolve_scale(state.snapshot(), 2.0), (1.25, 1, "webview"));
        state.apply(1.75, version(100.0, 5));
        assert_eq!(resolve_scale(state.snapshot(), 2.0), (1.75, 2, "webview"));
    }

    #[test]
    fn reload_rejects_old_page_and_duplicate_reports() {
        let state = PetWebviewState::default();
        state.apply(1.75, version(100.0, 20));
        state.apply(1.25, version(200.0, 1));
        state.apply(1.75, version(100.0, 21));
        state.apply(2.0, version(200.0, 1));
        assert_eq!(resolve_scale(state.snapshot(), 2.0), (1.25, 2, "webview"));
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
    fn invalid_reports_do_not_poison_value_or_version() {
        assert_eq!(resolve_scale(None, 1.75), (1.75, 0, "fallback"));
        let state = PetWebviewState::default();
        for dpr in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            state.apply(dpr, version(100.0, 99));
        }
        state.apply(2.0, version(f64::NAN, 99));
        state.apply(2.0, version(100.0, 0));
        state.apply(1.25, version(100.0, 1));
        assert_eq!(resolve_scale(state.snapshot(), 2.0), (1.25, 1, "webview"));
    }
}
