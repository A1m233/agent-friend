use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const EVENT_LIMIT: usize = 4096;
pub const HISTORY_LIMIT: u64 = 16 * 1024 * 1024;
pub const INCIDENT_LIMIT: u64 = 8 * 1024 * 1024;
pub const CONTEXT_LIMIT: usize = 256 * 1024;
pub const INCIDENT_FILE_LIMIT: usize = 512 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Sample {
    pub page_id: String,
    pub renderer_id: String,
    pub backend: Backend,
    pub kind: SampleKind,
    pub sequence: u64,
    pub elapsed_ms: f64,
    pub interval_ms: f64,
    pub ticks: u32,
    pub renders: u32,
    pub timer_ticks: u32,
    pub max_render_ms: f64,
    pub max_tick_gap_ms: f64,
    pub visible: bool,
    pub ticker_started: bool,
    pub context_lost: bool,
    pub width: u32,
    pub height: u32,
    pub dpr: f64,
    pub dropped: u32,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Backend {
    Pixi7,
    Pixi8,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum SampleKind {
    Started,
    Summary,
    ContextLost,
    ContextRestored,
    Visibility,
    Stopped,
    ObserverFailed,
}

pub fn valid_id(id: &str) -> bool {
    id.len() == 36 && id.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-')
}
impl Sample {
    pub fn valid(&self) -> bool {
        valid_id(&self.page_id)
            && valid_id(&self.renderer_id)
            && [
                self.elapsed_ms,
                self.interval_ms,
                self.max_render_ms,
                self.max_tick_gap_ms,
                self.dpr,
            ]
            .iter()
            .all(|n| n.is_finite() && *n >= 0.0 && *n <= 1e12)
            && self.dpr > 0.0
            && self.dpr <= 32.0
            && self.width <= 100_000
            && self.height <= 100_000
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Record {
    pub schema_version: u8,
    pub process_session_id: String,
    pub identity: Value,
    pub record_seq: u64,
    pub recorded_at: i64,
    pub monotonic_ms: u64,
    pub event: Value,
}

fn keys(value: &Value, allowed: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|o| o.keys().all(|k| allowed.contains(&k.as_str())))
}
fn code(value: &Value) -> bool {
    value.as_str().is_some_and(|s| {
        !s.is_empty()
            && s.len() <= 128
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.:+".contains(&b))
    })
}
fn state(value: &Value) -> bool {
    keys(
        value,
        &[
            "visible",
            "position",
            "size",
            "scale",
            "hwnd",
            "style",
            "extendedStyle",
        ],
    ) && value.as_object().is_some_and(|o| {
        o.iter().all(|(k, v)| match k.as_str() {
            "position" => {
                v.is_null()
                    || (keys(v, &["x", "y"])
                        && v.as_object()
                            .is_some_and(|o| o.values().all(Value::is_number)))
            }
            "size" => {
                v.is_null()
                    || (keys(v, &["width", "height"])
                        && v.as_object()
                            .is_some_and(|o| o.values().all(Value::is_number)))
            }
            "visible" => v.is_null() || v.is_boolean(),
            "hwnd" => v.is_null() || code(v),
            _ => v.is_null() || v.is_number(),
        })
    })
}
pub fn valid_event(value: &Value) -> bool {
    match value["kind"].as_str() {
        Some("render-sample") => {
            keys(value, &["kind", "sample"])
                && serde_json::from_value::<Sample>(value["sample"].clone())
                    .is_ok_and(|s| s.valid())
        }
        Some("process-start" | "process-end" | "user-mark") => keys(value, &["kind"]),
        Some("observation-gap") => {
            keys(value, &["kind", "presentation"]) && value["presentation"] == "unknown"
        }
        Some("window-observation") => {
            keys(value, &["kind", "reason", "state"])
                && code(&value["reason"])
                && state(&value["state"])
        }
        Some("window-operation") => {
            keys(
                value,
                &[
                    "kind", "action", "reason", "outcome", "window", "before", "after",
                ],
            ) && ["action", "reason", "outcome", "window"]
                .iter()
                .all(|k| code(&value[*k]))
                && state(&value["before"])
                && state(&value["after"])
        }
        Some("webview-processes") => {
            keys(value, &["kind", "scope", "processes", "available"])
                && value["scope"] == "environment"
                && value["available"].is_boolean()
                && value["processes"].as_array().is_some_and(|a| {
                    a.len() <= 64
                        && a.iter().all(|p| {
                            keys(p, &["pid", "kind"]) && p["pid"].is_u64() && p["kind"].is_i64()
                        })
                })
        }
        Some("display-state") => keys(value, &["kind", "state"]) && code(&value["state"]),
        _ => false,
    }
}
impl Record {
    pub fn valid(&self) -> bool {
        self.schema_version == 1
            && code(&Value::String(self.process_session_id.clone()))
            && keys(
                &self.identity,
                &[
                    "buildMode",
                    "platform",
                    "appVersion",
                    "buildId",
                    "sourceCommit",
                    "sourceDirty",
                    "webviewVersion",
                ],
            )
            && self.identity.as_object().is_some_and(|o| {
                o.iter().all(|(k, v)| {
                    if k == "sourceDirty" {
                        v.is_boolean()
                    } else {
                        code(v)
                    }
                })
            })
            && valid_event(&self.event)
    }
}

/// Signals describe missing progress, never whether displayed pixels are correct.
#[derive(Default)]
pub struct Detector {
    renderer: String,
    sequence: u64,
    stalled_ms: f64,
}
impl Detector {
    pub fn observe(&mut self, s: &Sample) -> Option<&'static str> {
        if s.renderer_id != self.renderer {
            self.renderer.clone_from(&s.renderer_id);
            self.sequence = 0;
            self.stalled_ms = 0.0;
        }
        if s.sequence <= self.sequence {
            return None;
        }
        self.sequence = s.sequence;
        if s.kind == SampleKind::ContextLost {
            return Some("context-lost");
        }
        if s.kind == SampleKind::ObserverFailed {
            return Some("observer-failed");
        }
        if s.kind != SampleKind::Summary
            || !s.visible
            || !s.ticker_started
            || s.context_lost
            || s.elapsed_ms < 15_000.0
            || s.dropped != 0
        {
            self.stalled_ms = 0.0;
            return None;
        }
        if s.renders > 0 {
            self.stalled_ms = 0.0;
            return None;
        }
        // Gaps in communication do not count as time spent observing a stopped renderer.
        self.stalled_ms += s.interval_ms.min(6_000.0);
        if self.stalled_ms >= 15_000.0 {
            self.stalled_ms = 0.0;
            Some("render-progress-missing")
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample() -> Sample {
        serde_json::from_value(serde_json::json!({"pageId":"00000000-0000-0000-0000-000000000001",
            "rendererId":"00000000-0000-0000-0000-000000000002","backend":"pixi7","kind":"summary",
            "sequence":1,"elapsedMs":20000,"intervalMs":5000,"ticks":100,"renders":0,"timerTicks":5,
            "maxRenderMs":0,"maxTickGapMs":16,"visible":true,"tickerStarted":true,"contextLost":false,
            "width":4000,"height":2560,"dpr":1.25,"dropped":0})).unwrap()
    }
    #[test]
    fn detects_stopped_render_with_live_page_and_resets_on_hidden() {
        let mut d = Detector::default();
        let mut s = sample();
        assert_eq!(d.observe(&s), None);
        s.sequence += 1;
        assert_eq!(d.observe(&s), None);
        s.sequence += 1;
        assert_eq!(d.observe(&s), Some("render-progress-missing"));
        s.visible = false;
        for _ in 0..10 {
            s.sequence += 1;
            assert_eq!(d.observe(&s), None);
        }
    }
    #[test]
    fn reordered_or_dropped_samples_do_not_prove_stall() {
        let mut d = Detector::default();
        let mut s = sample();
        for _ in 0..10 {
            assert_eq!(d.observe(&s), None);
        }
        for _ in 0..10 {
            s.sequence += 1;
            s.dropped = 1;
            assert_eq!(d.observe(&s), None);
        }
    }
    #[test]
    fn rejects_content_and_invalid_numbers() {
        let mut v = serde_json::to_value(sample()).unwrap();
        v["content"] = "private".into();
        assert!(serde_json::from_value::<Sample>(v).is_err());
        let mut s = sample();
        s.dpr = f64::NAN;
        assert!(!s.valid());
    }
}
