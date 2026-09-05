//! Read-only export. No page IPC, writer lock, flush request, or business action is needed.
use super::{
    journal::{self, HISTORY_BYTES, INCIDENT_BYTES, INCIDENT_FILE_BYTES, SEGMENT_BYTES},
    manager::ACTUAL_QUEUE_BYTES,
    model,
};
use chrono::Utc;
use serde_json::{json, Map, Value};
use std::{io::Read, path::Path};

pub(crate) struct Artifact {
    pub name: String,
    pub bytes: Vec<u8>,
}
pub(crate) struct Export {
    pub artifacts: Vec<Artifact>,
    pub summary: Value,
    pub failures: Vec<String>,
}
impl Export {
    pub(crate) fn unavailable() -> Self {
        finish(
            Vec::new(),
            Vec::new(),
            Vec::new(),
            json!({}),
            vec![json!({"kind":"read-failed","scope":"collector"})],
            vec!["pet input evidence read-failed".into()],
            "read-failed",
        )
    }
}
pub(crate) fn export_failed(_reason: &str) -> Export {
    Export::unavailable()
}

/// Optional native atomics only. It does not wait for, or stand in for, persisted history.
pub(crate) fn attach_current(output: &mut Export, current: Value) {
    let valid = current.as_object().is_some_and(|object| object.len() == 7)
        && current["scope"] == "export-time"
        && current["processSessionId"]
            .as_str()
            .is_some_and(model::numeric_id)
        && current["queuedBytes"]
            .as_u64()
            .is_some_and(|n| n <= ACTUAL_QUEUE_BYTES as u64)
        && ["dropped", "invalid", "writeFailed"]
            .iter()
            .all(|key| current[*key].as_u64().is_some())
        && current["workerStopped"].is_boolean();
    if !valid {
        output
            .failures
            .push("pet input evidence current read-failed".into());
        output.summary["gaps"]
            .as_array_mut()
            .expect("producer coverage")
            .push(json!({"kind":"read-failed","scope":"collector"}));
    } else {
        let bytes = serde_json::to_vec(&current).expect("bounded scalar snapshot");
        let used = output
            .artifacts
            .iter()
            .map(|artifact| artifact.bytes.len())
            .sum::<usize>();
        if used.saturating_add(bytes.len()).saturating_add(1024) > model::EXPORT_BYTES {
            output.summary["gaps"]
                .as_array_mut()
                .expect("producer coverage")
                .push(json!({"kind":"truncated","scope":"collector"}));
        } else {
            output
                .artifacts
                .retain(|artifact| artifact.name != "diagnostics/pet-input/current.json");
            output.artifacts.push(Artifact {
                name: "diagnostics/pet-input/current.json".into(),
                bytes,
            });
        }
        if let Some(count) = current["writeFailed"].as_u64().filter(|count| *count > 0) {
            output
                .failures
                .push("pet input evidence write-failed".into());
            output.summary["gaps"]
                .as_array_mut()
                .expect("producer coverage")
                .push(json!({"kind":"write-failed","scope":"collector","count":count}));
        }
    }
    output.failures.sort();
    output.failures.dedup();
    output.summary["failureCount"] = json!(output.failures.len());
    replace_coverage(output);
}

pub(crate) fn export_for_bundle(directory: &Path) -> Export {
    if !directory.exists() {
        return finish(
            Vec::new(),
            Vec::new(),
            Vec::new(),
            json!({}),
            vec![
                json!({"kind":"not-collected","scope":"collector"}),
                json!({"kind":"unknown","scope":"incident"}),
            ],
            Vec::new(),
            "not-collected",
        );
    }
    let mut artifacts = Vec::new();
    let mut descriptions = Vec::new();
    let mut identities = Vec::new();
    let mut failures = Vec::new();
    let mut gaps = Vec::new();
    let files = match journal::scan(directory) {
        Ok(files) => files,
        Err(_) => return Export::unavailable(),
    };
    let now = Utc::now().timestamp_millis();
    let mut total = 0usize;
    let mut remaining_read_bytes = model::EXPORT_BYTES;
    let mut history_bytes = 0u64;
    let mut incident_bytes = 0u64;
    let mut incident_count = 0usize;
    let mut retained_incidents = Vec::new();
    for file in files {
        let max_age = if file.kind == "history" { 7 } else { 30 } * 86_400_000i64;
        if now.saturating_sub(file.created_ms) > max_age {
            gaps.push(json!({"kind":"expired","scope":file.kind,"count":1}));
            continue;
        }
        if file.created_ms > now {
            gaps.push(json!({"kind":"clock-uncertain","scope":file.kind}));
        }
        let limit = if file.kind == "history" {
            SEGMENT_BYTES as usize
        } else {
            INCIDENT_FILE_BYTES
        };
        // Metadata and handles are obtained together; read only this handle's frozen byte boundary.
        let (mut bytes, frozen) = match read_frozen(&file.path, limit, &mut remaining_read_bytes) {
            Ok(value) => value,
            Err("read-budget") => {
                gaps.push(json!({"kind":"truncated","scope":"collector"}));
                break;
            }
            Err(_) => {
                failures.push("pet input evidence read-failed".into());
                gaps.push(json!({"kind":"read-failed","scope":file.kind}));
                continue;
            }
        };
        if total.saturating_add(bytes.len()) > model::EXPORT_BYTES - 128 * 1024
            || (file.kind == "history"
                && history_bytes.saturating_add(bytes.len() as u64) > HISTORY_BYTES)
            || (file.kind == "incident"
                && (incident_bytes.saturating_add(bytes.len() as u64) > INCIDENT_BYTES
                    || incident_count >= 32))
        {
            gaps.push(json!({"kind":"truncated","scope":file.kind,"count":1}));
            continue;
        }
        let index = descriptions.len() + 1;
        let entry = format!(
            "diagnostics/pet-input/{}-{:03}.{}",
            file.kind,
            index,
            if file.kind == "history" {
                "jsonl"
            } else {
                "json"
            }
        );
        let mut status = "available";
        let (session, identity, first_seq, last_seq, first_at, last_at) = if file.kind == "history"
        {
            if !bytes.ends_with(b"\n") {
                let end = bytes
                    .iter()
                    .rposition(|byte| *byte == b'\n')
                    .map(|p| p + 1)
                    .unwrap_or(0);
                bytes.truncate(end);
                status = "corrupt-tail";
                gaps.push(json!({"kind":"corrupt-tail","scope":"history"}));
                failures.push("pet input evidence corrupt-tail".into());
            }
            let mut lines = bytes.split(|b| *b == b'\n').filter(|line| !line.is_empty());
            let Some(header) = lines
                .next()
                .and_then(|line| serde_json::from_slice::<Value>(line).ok())
            else {
                gaps.push(json!({"kind":"read-failed","scope":"history"}));
                failures.push("pet input evidence invalid-header".into());
                continue;
            };
            if !valid_header(&header) {
                gaps.push(json!({"kind":"read-failed","scope":"history"}));
                failures.push("pet input evidence invalid-header".into());
                continue;
            }
            let mut first_seq = Value::Null;
            let mut last_seq = Value::Null;
            let mut first_at = Value::Null;
            let mut last_at = Value::Null;
            let mut valid = true;
            for line in lines {
                if line.len() > model::EVENT_BYTES {
                    valid = false;
                    break;
                }
                let Ok(record) = serde_json::from_slice::<Value>(line) else {
                    valid = false;
                    break;
                };
                if !valid_record(&record)
                    || record["processSessionId"] != header["processSessionId"]
                {
                    valid = false;
                    break;
                }
                if first_seq.is_null() {
                    first_seq = record["recordSeq"].clone();
                    first_at = record["recordedAt"].clone();
                }
                last_seq = record["recordSeq"].clone();
                last_at = record["recordedAt"].clone();
            }
            if !valid {
                gaps.push(json!({"kind":"read-failed","scope":"history"}));
                failures.push("pet input evidence invalid-record".into());
                continue;
            }
            history_bytes += bytes.len() as u64;
            (
                header["processSessionId"].clone(),
                header["identity"].clone(),
                first_seq,
                last_seq,
                first_at,
                last_at,
            )
        } else {
            let Ok(incident) = serde_json::from_slice::<Value>(&bytes) else {
                failures.push("pet input evidence invalid-incident".into());
                continue;
            };
            if !valid_incident(&incident) {
                gaps.push(json!({"kind":"read-failed","scope":"incident"}));
                failures.push("pet input evidence invalid-incident".into());
                continue;
            }
            let mut summary = Map::new();
            for key in [
                "incidentId",
                "processSessionId",
                "pageInstanceId",
                "signal",
                "firstRecordedAt",
                "lastRecordedAt",
                "count",
                "recovery",
                "closed",
                "truncated",
            ] {
                summary.insert(key.into(), incident[key].clone());
            }
            retained_incidents.push(Value::Object(summary));
            if incident["truncated"] == true {
                gaps.push(json!({"kind":"truncated","scope":"incident"}));
            }
            incident_bytes += bytes.len() as u64;
            incident_count += 1;
            (
                incident["processSessionId"].clone(),
                incident["identity"].clone(),
                Value::Null,
                Value::Null,
                incident["firstRecordedAt"].clone(),
                incident["lastRecordedAt"].clone(),
            )
        };
        let occurrence = json!({"processSessionId":session,"identity":identity});
        if !identities.contains(&occurrence) {
            identities.push(occurrence);
        }
        total += bytes.len();
        descriptions.push(json!({"entry":entry,"kind":file.kind,"bytes":bytes.len(),"frozenBytes":frozen,"processSessionId":session,
            "firstRecordSeq":first_seq,"lastRecordSeq":last_seq,"firstRecordedAt":first_at,"lastRecordedAt":last_at,"status":status}));
        artifacts.push(Artifact { name: entry, bytes });
    }
    let state_path = directory.join("coverage-state.json");
    let state = if state_path.exists() {
        match read_frozen(&state_path, 128 * 1024, &mut remaining_read_bytes)
            .and_then(|(bytes, _)| serde_json::from_slice(&bytes).map_err(|_| "read-failed"))
        {
            Ok(value) => value,
            Err("read-budget") => {
                gaps.push(json!({"kind":"truncated","scope":"metadata"}));
                json!({})
            }
            Err(_) => {
                gaps.push(json!({"kind":"read-failed","scope":"metadata"}));
                failures.push("pet input evidence metadata read-failed".into());
                json!({})
            }
        }
    } else {
        gaps.push(json!({"kind":"unknown","scope":"metadata"}));
        json!({})
    };
    let status = if descriptions.is_empty() {
        "not-covered"
    } else {
        "available"
    };
    let mut output = finish(
        artifacts,
        descriptions,
        identities,
        state,
        gaps,
        failures,
        status,
    );
    output.summary["retainedIncidents"] = json!(retained_incidents);
    replace_coverage(&mut output);
    output
}

fn read_frozen(
    path: &Path,
    limit: usize,
    remaining: &mut usize,
) -> Result<(Vec<u8>, u64), &'static str> {
    if !journal::safe_regular(path).map_err(|_| "read-failed")? {
        return Err("read-failed");
    }
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000);
    }
    let file = options.open(path).map_err(|_| "read-failed")?;
    let metadata = file.metadata().map_err(|_| "read-failed")?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err("read-failed");
        }
    }
    if !metadata.is_file() || metadata.len() > limit as u64 {
        return Err("read-failed");
    }
    let frozen = metadata.len();
    if frozen > *remaining as u64 {
        return Err("read-budget");
    }
    // Charge even malformed/discarded content and partial failed reads to the shared budget.
    *remaining -= frozen as usize;
    let mut bytes = Vec::with_capacity(frozen as usize);
    file.take(frozen)
        .read_to_end(&mut bytes)
        .map_err(|_| "read-failed")?;
    if bytes.len() != frozen as usize {
        return Err("read-failed");
    }
    Ok((bytes, frozen))
}

fn valid_time(value: &Value) -> bool {
    value
        .as_str()
        .is_some_and(|s| s.len() <= 40 && chrono::DateTime::parse_from_rfc3339(s).is_ok())
}
fn valid_identity(value: &Value) -> bool {
    &model::identity(value) == value && value["identityComplete"].is_boolean()
}
fn valid_header(value: &Value) -> bool {
    value["schemaVersion"] == 1
        && value["kind"] == "segment-header"
        && value["source"] == "collector"
        && value["processSessionId"]
            .as_str()
            .is_some_and(model::numeric_id)
        && valid_identity(&value["identity"])
        && valid_time(&value["recordedAt"])
        && value["recordSeq"].as_u64().is_some()
        && value["createdAtMs"].as_i64().is_some()
        && value.as_object().is_some_and(|v| v.len() == 8)
}
fn valid_record(value: &Value) -> bool {
    if value["schemaVersion"] != 1
        || !value["processSessionId"]
            .as_str()
            .is_some_and(model::numeric_id)
        || value["recordSeq"].as_u64().is_none()
        || !valid_time(&value["recordedAt"])
        || value["processMonotonicMs"].as_u64().is_none()
    {
        return false;
    }
    let mut event = value.clone();
    let Some(object) = event.as_object_mut() else {
        return false;
    };
    for key in [
        "schemaVersion",
        "processSessionId",
        "recordSeq",
        "recordedAt",
        "processMonotonicMs",
    ] {
        object.remove(key);
    }
    if let Some(seq) = object.remove("ingressSeq") {
        if seq.as_u64().is_none() {
            return false;
        }
    }
    if value["source"] == "collector" {
        if !matches!(
            value["kind"].as_str(),
            Some("process-start" | "process-end" | "coverage-heartbeat")
        ) {
            return false;
        }
        return object.iter().all(|(key, value)| match key.as_str() {
            "source" | "kind" => true,
            "lastPageReport" | "latestEvidence" => valid_position(value, true),
            _ => false,
        });
    }
    model::validate_event(&event)
}
fn valid_position(value: &Value, page: bool) -> bool {
    value.is_null()
        || value.as_object().is_some_and(|fields| {
            fields.iter().all(|(key, value)| match key.as_str() {
                "processSessionId" => value.as_str().is_some_and(model::numeric_id),
                "recordSeq" | "frontendSeq" => value.as_u64().is_some(),
                "recordedAt" => valid_time(value),
                "pageInstanceId" if page => model::correlation_id(value),
                _ => false,
            })
        })
}
fn valid_incident(value: &Value) -> bool {
    let Some(fields) = value.as_object() else {
        return false;
    };
    if fields.len() != 14
        || value["schemaVersion"] != 1
        || !valid_identity(&value["identity"])
        || !value["processSessionId"]
            .as_str()
            .is_some_and(model::numeric_id)
        || !value["incidentId"].as_str().is_some_and(model::numeric_id)
        || !(value["pageInstanceId"].is_null() || model::correlation_id(&value["pageInstanceId"]))
        || !matches!(
            value["signal"].as_str(),
            Some(
                "dpr-mismatch"
                    | "dpr-command-failed"
                    | "passthrough-command-failed"
                    | "passthrough-verification-failed"
            )
        )
        || !valid_time(&value["firstRecordedAt"])
        || !valid_time(&value["lastRecordedAt"])
        || value["count"].as_u64().is_none()
        || !matches!(value["recovery"].as_str(), Some("observed" | "unknown"))
        || !value["closed"].is_boolean()
        || !value["truncated"].is_boolean()
        || !valid_record(&value["firstSignal"])
    {
        return false;
    }
    value["context"]
        .as_array()
        .is_some_and(|events| events.len() <= 4096 && events.iter().all(valid_record))
}

fn finish(
    artifacts: Vec<Artifact>,
    descriptions: Vec<Value>,
    identities: Vec<Value>,
    state: Value,
    mut gaps: Vec<Value>,
    failures: Vec<String>,
    status: &str,
) -> Export {
    let mut counters = Map::new();
    for key in [
        "dropped",
        "invalid",
        "rateLimited",
        "writeFailed",
        "evicted",
        "expired",
        "aggregated",
        "frontendDropped",
    ] {
        if let Some(count) = state["counters"][key].as_u64() {
            counters.insert(key.into(), json!(count));
            if count > 0 {
                let kind = match key {
                    "dropped" | "frontendDropped" => "dropped",
                    "invalid" => "dropped",
                    "rateLimited" => "rate-limited",
                    "writeFailed" => "write-failed",
                    "evicted" => "evicted",
                    "expired" => "expired",
                    _ => "truncated",
                };
                gaps.push(json!({"kind":kind,"scope":"collector","count":count}));
            }
        }
    }
    if let Some(folded) = state["metadataFolded"].as_u64() {
        counters.insert("metadataFolded".into(), json!(folded));
        if folded > 0 {
            gaps.push(json!({"kind":"truncated","scope":"metadata","count":folded}));
        }
    }
    if state["clockUncertain"] == true {
        gaps.push(json!({"kind":"clock-uncertain","scope":"collector"}));
    }
    let mut durability = json!({"scope":"persisted-only","tailCompleteness":"unknown","normalFlushMs":1000,"syncRequestMs":5000,"normalEnd":state["normalEnd"].as_bool()});
    for key in ["lastWritten", "lastSyncRequested"] {
        if valid_position(&state[key], false) {
            durability[key] = state[key].clone();
        }
    }
    let range = &state["lastDroppedRange"];
    if range["processSessionId"]
        .as_str()
        .is_some_and(model::numeric_id)
        && range["firstIngressSeq"].as_u64().is_some()
        && range["lastIngressSeq"].as_u64().is_some()
        && range["interior"] == "not-necessarily-contiguous"
    {
        durability["lastDroppedRange"] = json!({"processSessionId":range["processSessionId"],
            "firstIngressSeq":range["firstIngressSeq"],"lastIngressSeq":range["lastIngressSeq"],"interior":"not-necessarily-contiguous"});
    }
    let evictions = state["evictions"].as_array().into_iter().flatten().take(256).filter_map(|item| {
        (matches!(item["kind"].as_str(),Some("expired"|"evicted"))
            && matches!(item["scope"].as_str(),Some("history"|"incident"))
            && item["bytes"].as_u64().is_some() && item["createdAtMs"].as_i64().is_some()).then(||
                json!({"kind":item["kind"],"scope":item["scope"],"bytes":item["bytes"],"createdAtMs":item["createdAtMs"]}))
    }).collect::<Vec<_>>();
    if state["normalEnd"] != true {
        gaps.push(json!({"kind":"unknown","scope":"collector"}));
    }
    let mut limits = model::limits_json();
    limits["queueBytes"] = json!(ACTUAL_QUEUE_BYTES);
    limits["approvedMaxQueueBytes"] = json!(model::QUEUE_BYTES);
    let mut failures = failures;
    if state["metadataUnavailable"] == true {
        gaps.push(json!({"kind":"read-failed","scope":"metadata"}));
        failures.push("pet input evidence previous metadata read-failed".into());
    }
    if counters
        .get("writeFailed")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        > 0
    {
        failures.push("pet input evidence write-failed".into());
    }
    failures.sort();
    failures.dedup();
    let summary = json!({"schemaVersion":1,"status":status,"artifacts":descriptions,"identities":identities,"counters":counters,
        "limits":limits,"durability":durability,"gaps":gaps,"evictions":evictions,"retainedIncidents":[],"exportBytes":0,"failureCount":failures.len()});
    let mut output = Export {
        artifacts,
        summary,
        failures,
    };
    replace_coverage(&mut output);
    output
}
fn replace_coverage(output: &mut Export) {
    output
        .artifacts
        .retain(|artifact| artifact.name != "diagnostics/pet-input/coverage.json");
    let data_bytes = output
        .artifacts
        .iter()
        .map(|artifact| artifact.bytes.len())
        .sum::<usize>();
    // Iterate because the decimal size field includes the coverage entry itself.
    for _ in 0..4 {
        let bytes = serde_json::to_vec(&output.summary).unwrap_or_default();
        output.summary["exportBytes"] = json!(data_bytes + bytes.len());
    }
    let bytes = serde_json::to_vec(&output.summary).unwrap_or_default();
    output.artifacts.push(Artifact {
        name: "diagnostics/pet-input/coverage.json".into(),
        bytes,
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn missing_directory_always_has_coverage_without_claiming_no_incident() {
        let path = std::env::temp_dir().join(format!(
            "pet-evidence-missing-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let export = export_for_bundle(&path);
        assert_eq!(export.summary["status"], "not-collected");
        assert!(export.failures.is_empty());
        assert_eq!(export.artifacts.len(), 1);
        assert!(export.summary["gaps"]
            .as_array()
            .unwrap()
            .iter()
            .any(|gap| gap["kind"] == "unknown"));
    }
    #[test]
    fn unavailable_writer_current_failure_is_partial_without_fabricating_history() {
        let directory = std::env::temp_dir().join(format!(
            "pet-evidence-unwritten-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let mut output = export_for_bundle(&directory);
        attach_current(
            &mut output,
            json!({"scope":"export-time","processSessionId":"123-456","queuedBytes":0,"dropped":1,"invalid":0,"writeFailed":3,"workerStopped":false}),
        );
        assert_eq!(output.summary["status"], "not-collected");
        assert!(!output.failures.is_empty());
        assert!(output.summary["identities"].as_array().unwrap().is_empty());
        assert_eq!(output.summary["artifacts"].as_array().unwrap().len(), 0);
        assert!(output
            .artifacts
            .iter()
            .any(|artifact| artifact.name.ends_with("current.json")));
        assert_eq!(
            output.summary["exportBytes"].as_u64().unwrap(),
            output
                .artifacts
                .iter()
                .map(|artifact| artifact.bytes.len() as u64)
                .sum::<u64>()
        );
    }
    #[test]
    fn current_snapshot_cannot_smuggle_content() {
        let mut output = Export::unavailable();
        attach_current(
            &mut output,
            json!({"scope":"export-time","processSessionId":"123-456","queuedBytes":0,"dropped":0,"invalid":0,"writeFailed":0,"workerStopped":false,"credential":"DO-NOT-EXPORT"}),
        );
        assert!(!output
            .artifacts
            .iter()
            .any(|artifact| artifact.name.ends_with("current.json")));
        for artifact in output.artifacts {
            assert!(!String::from_utf8_lossy(&artifact.bytes).contains("DO-NOT-EXPORT"));
        }
    }
    #[test]
    fn discarded_invalid_files_still_exhaust_the_total_read_budget() {
        let directory = std::env::temp_dir().join(format!(
            "pet-evidence-read-budget-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&directory).unwrap();
        let mut invalid = vec![b'x'; 1024 * 1024];
        *invalid.last_mut().unwrap() = b'\n';
        let now = Utc::now().timestamp_millis();
        for index in 0..26 {
            std::fs::write(
                directory.join(format!("history-1-1-{index}-{now}.jsonl")),
                &invalid,
            )
            .unwrap();
        }
        let result = export_for_bundle(&directory);
        assert!(result.summary["gaps"]
            .as_array()
            .unwrap()
            .iter()
            .any(|gap| gap["kind"] == "truncated"));
        assert_eq!(result.artifacts.len(), 1);
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn frozen_handle_reader_charges_invalid_bytes_before_interpretation() {
        let path = std::env::temp_dir().join(format!(
            "pet-evidence-reader-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::write(&path, b"not-json").unwrap();
        let mut remaining = 10;
        let (bytes, _) = read_frozen(&path, 100, &mut remaining).unwrap();
        assert!(serde_json::from_slice::<Value>(&bytes).is_err());
        assert_eq!(remaining, 2);
        assert_eq!(
            read_frozen(&path, 100, &mut remaining).unwrap_err(),
            "read-budget"
        );
        assert_eq!(remaining, 2);
        std::fs::remove_file(path).unwrap();
    }
}
