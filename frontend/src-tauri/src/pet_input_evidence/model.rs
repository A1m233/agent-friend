//! Producer-owned, content-free facts. Unknown fields are rejected, never serialized verbatim.
use serde_json::{json, Map, Value};

pub(super) const EVENT_BYTES: usize = 4 * 1024;
pub(super) const QUEUE_ENTRIES: usize = 256;
pub(super) const QUEUE_BYTES: usize = 1024 * 1024;
pub(super) const CONTEXT_BYTES: usize = 256 * 1024;
pub(super) const METADATA_BYTES: u64 = 1024 * 1024;
pub(super) const EXPORT_BYTES: usize = 25 * 1024 * 1024;
pub(super) const SCAN_FILES: usize = 512;

pub(super) fn limits_json() -> Value {
    json!({"historyBytes":16*1024*1024,"historyAgeDays":7,"segmentBytes":2*1024*1024,
        "incidentBytes":8*1024*1024,"incidentAgeDays":30,"incidentCount":32,
        "incidentFileBytes":256*1024,"eventBytes":EVENT_BYTES,"queueEntries":QUEUE_ENTRIES,
        "queueBytes":QUEUE_BYTES,"totalBytes":EXPORT_BYTES})
}

pub(super) fn numeric_id(value: &str) -> bool {
    !value.is_empty() && value.len() <= 96 && value.bytes().all(|c| c.is_ascii_digit() || c == b'-')
}

pub(super) fn correlation_id(value: &Value) -> bool {
    value.as_u64().is_some()
        || value.as_str().is_some_and(|s| {
            // UUIDs, numeric request IDs, and UUID + numeric suffixes only; no arbitrary labels.
            !s.is_empty() && s.len() <= 96 && s.bytes().all(|c| c.is_ascii_hexdigit() || c == b'-')
        })
}

fn choice(value: &Value, choices: &[&str]) -> bool {
    value.as_str().is_some_and(|s| choices.contains(&s))
}

fn number(value: &Value, low: f64, high: f64) -> bool {
    value
        .as_f64()
        .is_some_and(|n| n.is_finite() && n >= low && n <= high)
}

fn geometry(value: &Value, dimensions: bool, point: bool) -> bool {
    let Some(object) = value.as_object() else {
        return false;
    };
    object.len() == usize::from(dimensions) * 2 + usize::from(point) * 2
        && object.iter().all(|(key, value)| match key.as_str() {
            "x" | "y" if point => number(value, -1_000_000.0, 1_000_000.0),
            "width" | "height" if dimensions => number(value, 0.0, 1_000_000.0),
            _ => false,
        })
}

pub(super) const KINDS: &[&str] = &[
    "page-start",
    "page-end",
    "display-observed",
    "window-observed",
    "dpr-report",
    "dpr-ack",
    "dpr-applied",
    "cursor-observed",
    "hit-decision",
    "hit-summary",
    "passthrough-request",
    "passthrough-ack",
    "passthrough-applied",
    "coverage-gap",
];

pub(super) fn validate_event(value: &Value) -> bool {
    let Some(fields) = value.as_object() else {
        return false;
    };
    if !fields
        .get("source")
        .is_some_and(|v| choice(v, &["frontend", "native"]))
        || !fields.get("kind").is_some_and(|v| choice(v, KINDS))
    {
        return false;
    }
    if fields.get("source").and_then(Value::as_str) == Some("frontend")
        && (!fields.get("pageInstanceId").is_some_and(correlation_id)
            || !fields
                .get("frontendSeq")
                .is_some_and(|v| v.as_u64().is_some())
            || !fields
                .get("monotonicMs")
                .is_some_and(|v| number(v, 0.0, 1e15)))
    {
        return false;
    }
    fields.iter().all(|(key, value)| {
        if value.is_null() {
            return matches!(
                key.as_str(),
                "alphaHit"
                    | "verified"
                    | "styleBefore"
                    | "styleActual"
                    | "styleExpected"
                    | "errorCode"
            );
        }
        match key.as_str() {
            "source" => choice(value, &["frontend", "native"]),
            "kind" => choice(value, KINDS),
            "pageInstanceId" | "requestId" => correlation_id(value),
            "frontendSeq" | "displayRevision" | "sampleId" | "dprRevision" | "applySeq"
            | "styleBefore" | "styleExpected" | "styleActual" | "count" | "firstSeq"
            | "lastSeq" | "droppedCount" => value.as_u64().is_some(),
            "monotonicMs" => number(value, 0.0, 1e15),
            "actualDpr" | "displayDpr" | "reportedDpr" | "oldDpr" | "newDpr" | "scale"
            | "windowScaleFactor" => number(value, 0.0, 64.0),
            "scaleSource" => choice(value, &["webview", "fallback", "unknown"]),
            "outcome" => choice(
                value,
                &[
                    "applied",
                    "unchanged",
                    "not-applied",
                    "ok",
                    "error",
                    "partial",
                    "unknown",
                ],
            ),
            "reason" => choice(
                value,
                &[
                    "disabled",
                    "initial",
                    "dragging",
                    "dom-hit",
                    "sprite-hit",
                    "sprite-transparent",
                    "outside",
                ],
            ),
            "ignore" | "dragging" | "alphaTested" | "alphaHit" | "commandIssued" | "verified" => {
                value.is_boolean()
            }
            "stage" => choice(
                value,
                &["request", "apply", "refresh", "verify", "ack", "unknown"],
            ),
            "errorKind" => choice(
                value,
                &[
                    "invoke-failed",
                    "lock-failed",
                    "window-missing",
                    "style-write-failed",
                    "style-refresh-failed",
                    "style-verification-failed",
                    "unsupported",
                    "unknown",
                ],
            ),
            "errorCode" => number(value, i32::MIN as f64, u32::MAX as f64),
            "viewport" | "windowSize" => geometry(value, true, false),
            "windowOrigin" | "physical" | "css" => geometry(value, false, true),
            "spriteBounds" => geometry(value, true, true),
            _ => false,
        }
    })
}

pub(super) fn identity(value: &Value) -> Value {
    let mut output = Map::new();
    if let Some(fields) = value.as_object() {
        for (key, value) in fields {
            let valid = match key.as_str() {
                "sourceDirty" => value.is_boolean(),
                "platform" => choice(value, &["windows", "macos", "linux", "unknown"]),
                "buildMode" => choice(value, &["debug", "release", "dev", "installed", "unknown"]),
                "sourceCommit" => value.as_str().is_some_and(|s| {
                    (7..=64).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_hexdigit())
                }),
                "webviewVersion" | "appVersion" => value.as_str().is_some_and(|s| {
                    !s.is_empty()
                        && s.len() <= 48
                        && s.bytes()
                            .all(|c| c.is_ascii_digit() || c == b'.' || c == b'-')
                }),
                "buildId" => value.as_str().is_some_and(|s| {
                    !s.is_empty()
                        && s.len() <= 96
                        && s.bytes()
                            .all(|c| c.is_ascii_hexdigit() || matches!(c, b'T' | b'Z' | b'-'))
                }),
                _ => false,
            };
            if valid {
                output.insert(key.clone(), value.clone());
            }
        }
    }
    output.insert(
        "identityComplete".into(),
        Value::Bool(
            [
                "buildId",
                "sourceCommit",
                "sourceDirty",
                "platform",
                "webviewVersion",
            ]
            .iter()
            .all(|k| output.contains_key(*k)),
        ),
    );
    Value::Object(output)
}

pub(super) fn remove_coordinates(value: &mut Value) {
    if let Some(fields) = value.as_object_mut() {
        for field in ["physical", "css", "windowOrigin", "spriteBounds"] {
            fields.remove(field);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_content_and_non_numeric_geometry() {
        let good = json!({"source":"frontend","kind":"hit-decision","pageInstanceId":"ab12-1234","frontendSeq":1,"monotonicMs":0,"reason":"outside"});
        assert!(validate_event(&good));
        for field in [
            "domId",
            "text",
            "url",
            "path",
            "credential",
            "details",
            "error",
        ] {
            let mut bad = good.clone();
            bad[field] = json!("private content");
            assert!(!validate_event(&bad));
        }
        let mut bad = good.clone();
        bad["physical"] = json!({"x":"secret","y":1});
        assert!(!validate_event(&bad));
        let mut bad = good;
        bad["actualDpr"] = Value::Null;
        assert!(!validate_event(&bad));
    }
    #[test]
    fn identity_is_reconstructed_not_passed_through() {
        let result = identity(
            &json!({"path":"C:/secret","sourceCommit":"abc1234","platform":"windows","buildId":"token_secret","webviewVersion":"152.0.1"}),
        );
        assert_eq!(result["sourceCommit"], "abc1234");
        assert!(result.get("path").is_none());
        assert!(result.get("buildId").is_none());
        assert_eq!(result["identityComplete"], false);
    }
    #[test]
    fn all_declared_payload_buffers_fit_two_mib() {
        // Frontend buffer + IPC batch + native queue + shared precontext + one worker event.
        assert!(
            256 * 1024 + 64 * 1024 + QUEUE_BYTES + CONTEXT_BYTES + EVENT_BYTES <= 2 * 1024 * 1024
        );
    }
}
