use super::{
    export::export_for_bundle,
    incidents::Detector,
    journal::{self, Journal, Limits},
    model,
};
use chrono::Utc;
use serde_json::{json, Value};
use std::{
    fs,
    io::Write,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

struct Directory(PathBuf);
impl Directory {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!(
                "agent-friend-pet-input-test-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            )))
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn identity() -> Value {
    json!({"platform":"windows","buildMode":"debug","sourceCommit":"abcdef1234567","sourceDirty":false,"buildId":"20260905T000000Z-abcdef","webviewVersion":"152.0.1"})
}
fn event(session: &str, kind: &str, seq: u64, mono: u64) -> Value {
    json!({"schemaVersion":1,"source":"frontend","kind":kind,"processSessionId":session,"recordSeq":seq,
        "recordedAt":Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis,true),"processMonotonicMs":mono,
        "pageInstanceId":"abcd-1234","frontendSeq":seq,"monotonicMs":mono,"displayRevision":1})
}
fn sample(session: &str, seq: u64, mono: u64, scale: f64) -> Value {
    let mut event = event(session, "cursor-observed", seq, mono);
    event["sampleId"] = json!(seq);
    event["dprRevision"] = json!(1);
    event["scale"] = json!(scale);
    event["scaleSource"] = json!("webview");
    event["actualDpr"] = json!(1.25);
    event
}
fn native_application(seq: u64, mono: u64, page: &str, apply_seq: u64, failed: bool) -> Value {
    let mut value = event("100-1000", "passthrough-applied", seq, mono);
    value["source"] = json!("native");
    value["pageInstanceId"] = json!(page);
    value["requestId"] = json!(apply_seq);
    value["applySeq"] = json!(apply_seq);
    value["outcome"] = json!(if failed { "partial" } else { "applied" });
    value["verified"] = json!(!failed);
    value
}

#[test]
fn native_failure_before_page_start_merges_recovers_and_can_recur() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    let mut detector = Detector::default();
    submit(
        &mut writer,
        &mut detector,
        &native_application(1, 0, "abcd-1234", 1, true),
        0,
        now,
    );
    assert_eq!(incidents(&directory).len(), 1);
    submit(
        &mut writer,
        &mut detector,
        &event("100-1000", "page-start", 2, 100),
        100,
        now + 100,
    );
    submit(
        &mut writer,
        &mut detector,
        &native_application(3, 1100, "abcd-1234", 2, true),
        1100,
        now + 1100,
    );
    let pending = incidents(&directory);
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0]["count"], 2);
    assert_eq!(pending[0]["closed"], false);
    submit(
        &mut writer,
        &mut detector,
        &native_application(4, 1200, "abcd-1234", 3, false),
        1200,
        now + 1200,
    );
    let recovered = incidents(&directory);
    assert_eq!(recovered[0]["recovery"], "observed");
    assert_eq!(recovered[0]["closed"], true);
    submit(
        &mut writer,
        &mut detector,
        &native_application(5, 1300, "abcd-1234", 4, true),
        1300,
        now + 1300,
    );
    let recurring = incidents(&directory);
    assert_eq!(recurring.len(), 2);
    assert_ne!(recurring[0]["incidentId"], recurring[1]["incidentId"]);
}

#[test]
fn late_native_old_page_failure_and_recovery_cannot_touch_new_page_incident() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    let mut detector = Detector::default();
    submit(
        &mut writer,
        &mut detector,
        &event("100-1000", "page-start", 1, 0),
        0,
        now,
    );
    let mut new_page = event("100-1000", "page-start", 2, 100);
    new_page["pageInstanceId"] = json!("abcd-5678");
    submit(&mut writer, &mut detector, &new_page, 100, now + 100);
    submit(
        &mut writer,
        &mut detector,
        &native_application(3, 200, "abcd-5678", 10, true),
        200,
        now + 200,
    );
    submit(
        &mut writer,
        &mut detector,
        &native_application(4, 300, "abcd-1234", 4, true),
        300,
        now + 300,
    );
    submit(
        &mut writer,
        &mut detector,
        &native_application(5, 1400, "abcd-1234", 3, true),
        1400,
        now + 1400,
    );
    submit(
        &mut writer,
        &mut detector,
        &native_application(6, 1500, "abcd-1234", 4, false),
        1500,
        now + 1500,
    );
    let pending = incidents(&directory);
    assert_eq!(pending.len(), 2);
    let old = pending
        .iter()
        .find(|v| v["pageInstanceId"] == "abcd-1234")
        .unwrap();
    assert_eq!(old["count"], 2);
    assert_eq!(old["recovery"], "unknown");
    submit(
        &mut writer,
        &mut detector,
        &native_application(7, 1600, "abcd-1234", 5, false),
        1600,
        now + 1600,
    );
    let stored = incidents(&directory);
    let old = stored
        .iter()
        .find(|v| v["pageInstanceId"] == "abcd-1234")
        .unwrap();
    assert_eq!(old["recovery"], "observed");
    let current = stored
        .iter()
        .find(|v| v["pageInstanceId"] == "abcd-5678")
        .unwrap();
    assert_eq!(current["count"], 1);
    assert_eq!(current["closed"], false);
    assert_eq!(current["recovery"], "unknown");
    // The late native page is not made current: new-page samples still create its own mismatch.
    for (seq, mono) in [(8, 1800), (9, 2050), (10, 2300)] {
        let mut value = sample("100-1000", seq, mono, 1.75);
        value["pageInstanceId"] = json!("abcd-5678");
        submit(&mut writer, &mut detector, &value, mono, now + mono as i64);
    }
    assert!(incidents(&directory)
        .iter()
        .any(|v| v["signal"] == "dpr-mismatch" && v["pageInstanceId"] == "abcd-5678"));
}

#[test]
fn reordered_native_failure_does_not_lower_the_recovery_apply_sequence() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    let mut detector = Detector::default();
    for (seq, mono, apply, failed) in [(1, 0, 10, true), (2, 1100, 8, true), (3, 1200, 9, false)] {
        submit(
            &mut writer,
            &mut detector,
            &native_application(seq, mono, "abcd-1234", apply, failed),
            mono,
            now + mono as i64,
        );
    }
    assert_eq!(incidents(&directory)[0]["recovery"], "unknown");
    submit(
        &mut writer,
        &mut detector,
        &native_application(4, 1300, "abcd-1234", 11, false),
        1300,
        now + 1300,
    );
    let stored = incidents(&directory);
    assert_eq!(stored.len(), 1);
    assert_eq!(stored[0]["count"], 2);
    assert_eq!(stored[0]["recovery"], "observed");
}

#[test]
fn logical_twenty_four_hour_history_stays_bounded_without_replacing_incident() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let limits = Limits {
        history: 4096,
        segment: 1600,
        ..Limits::default()
    };
    let mut writer =
        Journal::open(directory.0.clone(), "100-1000".into(), identity(), limits).unwrap();
    let mut detector = Detector::default();
    submit(
        &mut writer,
        &mut detector,
        &native_application(1, 0, "abcd-1234", 1, true),
        0,
        now,
    );
    detector.end(&mut writer, 1, now + 1).unwrap();
    let original = incidents(&directory)[0].clone();
    let mut seq = 2;
    // Check every hour using the actual writer and bounded rotation, with scaled disk budgets.
    // Admission at 60 Hz over all 24 hours is exercised separately, without millions of disk IOs.
    for hour in 0..24u64 {
        for offset in 0..8u64 {
            let mono = hour * 3_600_000 + offset * 1000;
            writer
                .append(
                    &event("100-1000", "display-observed", seq, mono),
                    now + mono as i64,
                )
                .unwrap();
            seq += 1;
        }
        let files = journal::scan(&directory.0).unwrap();
        assert!(
            files
                .iter()
                .filter(|f| f.kind == "history")
                .map(|f| f.bytes)
                .sum::<u64>()
                <= limits.history
        );
        assert!(files
            .iter()
            .filter(|f| f.kind == "history")
            .all(|f| f.bytes <= limits.segment));
        assert!(
            files
                .iter()
                .filter(|f| f.kind == "incident")
                .map(|f| f.bytes)
                .sum::<u64>()
                <= limits.incidents
        );
    }
    writer.persist_state(true).unwrap();
    let retained = incidents(&directory);
    assert_eq!(retained.len(), 1);
    assert_eq!(retained[0], original);
    assert!(writer.state["counters"]["evicted"].as_u64().unwrap() > 0);
}
fn submit(journal: &mut Journal, detector: &mut Detector, event: &Value, mono: u64, now: i64) {
    journal.append(event, now).unwrap();
    detector.observe(journal, event, mono, now).unwrap();
}
fn incidents(directory: &Directory) -> Vec<Value> {
    let output = export_for_bundle(&directory.0);
    assert!(output.failures.is_empty(), "{:?}", output.failures);
    output
        .artifacts
        .iter()
        .filter(|a| a.name.contains("/incident-"))
        .map(|a| serde_json::from_slice(&a.bytes).unwrap())
        .collect()
}

#[test]
fn baseline_exists_without_detector_and_survives_distinct_writer_instances() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    {
        let mut writer = Journal::open(
            directory.0.clone(),
            "100-1000".into(),
            identity(),
            Limits::default(),
        )
        .unwrap();
        writer
            .append(&event("100-1000", "page-start", 1, 0), now)
            .unwrap();
        writer.flush(true).unwrap();
        writer.state["normalEnd"] = json!(true);
        writer.persist_state(true).unwrap();
    }
    {
        let mut writer = Journal::open(
            directory.0.clone(),
            "200-2000".into(),
            identity(),
            Limits::default(),
        )
        .unwrap();
        assert_eq!(writer.state["normalEnd"], false);
        writer
            .append(&event("200-2000", "page-start", 1, 0), now + 1)
            .unwrap();
        writer.flush(true).unwrap();
    }
    let output = export_for_bundle(&directory.0);
    assert!(output.failures.is_empty(), "{:?}", output.failures);
    assert_eq!(output.summary["status"], "available");
    assert_eq!(output.summary["identities"].as_array().unwrap().len(), 2);
    assert!(output.summary["retainedIncidents"]
        .as_array()
        .unwrap()
        .is_empty());
    assert_eq!(
        output.summary["exportBytes"].as_u64().unwrap(),
        output
            .artifacts
            .iter()
            .map(|a| a.bytes.len() as u64)
            .sum::<u64>()
    );
}

#[test]
fn mismatch_needs_three_distinct_samples_and_retains_recovery_and_recurrence() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    let mut detector = Detector::default();
    submit(
        &mut writer,
        &mut detector,
        &event("100-1000", "page-start", 1, 0),
        0,
        now,
    );
    for (seq, mono) in [(2, 100), (3, 350)] {
        submit(
            &mut writer,
            &mut detector,
            &sample("100-1000", seq, mono, 1.75),
            mono,
            now + mono as i64,
        );
    }
    assert!(incidents(&directory).is_empty());
    let mut duplicate = sample("100-1000", 4, 600, 1.75);
    duplicate["sampleId"] = json!(3);
    submit(&mut writer, &mut detector, &duplicate, 600, now + 600);
    assert!(incidents(&directory).is_empty());
    submit(
        &mut writer,
        &mut detector,
        &sample("100-1000", 5, 850, 1.75),
        850,
        now + 850,
    );
    assert_eq!(incidents(&directory).len(), 1);
    for (seq, mono) in [(6, 1100), (7, 3100)] {
        submit(
            &mut writer,
            &mut detector,
            &sample("100-1000", seq, mono, 1.25),
            mono,
            now + mono as i64,
        );
    }
    assert_eq!(incidents(&directory)[0]["recovery"], "observed");
    for (seq, mono) in [(8, 3400), (9, 3650), (10, 3900)] {
        submit(
            &mut writer,
            &mut detector,
            &sample("100-1000", seq, mono, 1.75),
            mono,
            now + mono as i64,
        );
    }
    let stored = incidents(&directory);
    assert_eq!(stored.len(), 2);
    assert_ne!(stored[0]["incidentId"], stored[1]["incidentId"]);
}

#[test]
fn healthy_history_rotation_never_evicts_retained_incident() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let limits = Limits {
        history: 2200,
        segment: 1200,
        ..Limits::default()
    };
    let mut writer =
        Journal::open(directory.0.clone(), "100-1000".into(), identity(), limits).unwrap();
    let mut detector = Detector::default();
    submit(
        &mut writer,
        &mut detector,
        &event("100-1000", "page-start", 1, 0),
        0,
        now,
    );
    let mut failure = event("100-1000", "dpr-ack", 2, 100);
    failure["outcome"] = json!("error");
    failure["requestId"] = json!(1);
    submit(&mut writer, &mut detector, &failure, 100, now + 100);
    detector.end(&mut writer, 101, now + 101).unwrap();
    for seq in 3..103 {
        writer
            .append(
                &event("100-1000", "display-observed", seq, seq),
                now + seq as i64,
            )
            .unwrap();
    }
    writer.persist_state(true).unwrap();
    assert_eq!(incidents(&directory).len(), 1);
    let files = journal::scan(&directory.0).unwrap();
    assert!(
        files
            .iter()
            .filter(|f| f.kind == "history")
            .map(|f| f.bytes)
            .sum::<u64>()
            <= limits.history,
        "history lengths {:?}, limit {}",
        files
            .iter()
            .filter(|f| f.kind == "history")
            .map(|f| f.bytes)
            .collect::<Vec<_>>(),
        limits.history
    );
    assert!(writer.state["counters"]["evicted"].as_u64().unwrap() > 0);
}

#[test]
fn post_context_is_limited_to_ten_seconds_but_signal_count_keeps_growing() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    let mut detector = Detector::default();
    submit(
        &mut writer,
        &mut detector,
        &event("100-1000", "page-start", 1, 0),
        0,
        now,
    );
    for (seq, mono) in [(2, 100), (3, 2000), (4, 9000), (5, 12_000), (6, 20_000)] {
        let mut failure = event("100-1000", "dpr-ack", seq, mono);
        failure["outcome"] = json!("error");
        failure["requestId"] = json!(seq);
        submit(
            &mut writer,
            &mut detector,
            &failure,
            mono,
            now + mono as i64,
        );
    }
    detector.end(&mut writer, 21_000, now + 21_000).unwrap();
    let stored = incidents(&directory);
    assert_eq!(stored.len(), 1);
    assert_eq!(stored[0]["count"], 5);
    assert_eq!(stored[0]["recovery"], "unknown");
    assert!(stored[0]["context"]
        .as_array()
        .unwrap()
        .iter()
        .all(|e| e["processMonotonicMs"].as_u64().unwrap() <= 10_100));
}

#[test]
fn late_old_page_does_not_change_new_page_incident() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    let mut detector = Detector::default();
    submit(
        &mut writer,
        &mut detector,
        &event("100-1000", "page-start", 1, 0),
        0,
        now,
    );
    let mut new_page = event("100-1000", "page-start", 1, 100);
    new_page["pageInstanceId"] = json!("abcd-5678");
    submit(&mut writer, &mut detector, &new_page, 100, now + 100);
    let mut stale = event("100-1000", "dpr-ack", 500, 200);
    stale["outcome"] = json!("error");
    submit(&mut writer, &mut detector, &stale, 200, now + 200);
    assert!(incidents(&directory).is_empty());
}

#[test]
fn incomplete_tail_is_reported_and_only_complete_lines_are_exported() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    {
        let mut writer = Journal::open(
            directory.0.clone(),
            "100-1000".into(),
            identity(),
            Limits::default(),
        )
        .unwrap();
        writer
            .append(&event("100-1000", "page-start", 1, 0), now)
            .unwrap();
        writer.flush(true).unwrap();
    }
    let path = journal::scan(&directory.0).unwrap().remove(0).path;
    fs::OpenOptions::new()
        .append(true)
        .open(path)
        .unwrap()
        .write_all(b"{\"private_unfinished")
        .unwrap();
    let output = export_for_bundle(&directory.0);
    assert!(!output.failures.is_empty());
    assert!(output.summary["gaps"]
        .as_array()
        .unwrap()
        .iter()
        .any(|g| g["kind"] == "corrupt-tail"));
    let history = output
        .artifacts
        .iter()
        .find(|a| a.name.contains("/history-"))
        .unwrap();
    assert!(history.bytes.ends_with(b"\n"));
    assert!(!String::from_utf8_lossy(&history.bytes).contains("private_unfinished"));
}

#[test]
fn active_segment_rotates_when_seven_day_max_age_is_crossed() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    writer
        .append(&event("100-1000", "page-start", 1, 0), now)
        .unwrap();
    writer
        .append(
            &event("100-1000", "display-observed", 2, 8 * 86_400_000),
            now + 8 * 86_400_000,
        )
        .unwrap();
    assert_eq!(journal::scan(&directory.0).unwrap().len(), 1);
    assert_eq!(writer.state["counters"]["expired"], 1);
}

#[test]
fn competing_writer_degrades_without_overwriting_and_export_does_not_wait_for_lock() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let mut writer = Journal::open(
        directory.0.clone(),
        "100-1000".into(),
        identity(),
        Limits::default(),
    )
    .unwrap();
    writer
        .append(&event("100-1000", "page-start", 1, 0), now)
        .unwrap();
    writer.flush(true).unwrap();
    assert!(Journal::open(
        directory.0.clone(),
        "200-2000".into(),
        identity(),
        Limits::default()
    )
    .is_err());
    let output = export_for_bundle(&directory.0);
    assert_eq!(output.summary["status"], "available");
    assert!(output.failures.is_empty());
}

#[test]
fn stored_content_injection_is_rejected_not_exported() {
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    {
        let mut writer = Journal::open(
            directory.0.clone(),
            "100-1000".into(),
            identity(),
            Limits::default(),
        )
        .unwrap();
        let mut injected = event("100-1000", "page-start", 1, 0);
        injected["credential"] = json!("DO-NOT-EXPORT");
        writer.append(&injected, now).unwrap();
        writer.flush(true).unwrap();
    }
    let output = export_for_bundle(&directory.0);
    assert!(!output.failures.is_empty());
    for artifact in output.artifacts {
        assert!(!String::from_utf8_lossy(&artifact.bytes).contains("DO-NOT-EXPORT"));
    }
}

#[test]
fn model_and_export_limits_include_headers_and_jsonl_delimiters() {
    assert_eq!(model::limits_json()["totalBytes"], 25 * 1024 * 1024);
    let directory = Directory::new();
    let now = Utc::now().timestamp_millis();
    let limits = Limits {
        history: 3200,
        segment: 1600,
        ..Limits::default()
    };
    let mut writer =
        Journal::open(directory.0.clone(), "100-1000".into(), identity(), limits).unwrap();
    for seq in 0..100 {
        writer
            .append(
                &event("100-1000", "display-observed", seq, seq),
                now + seq as i64,
            )
            .unwrap();
    }
    let files = journal::scan(&directory.0).unwrap();
    assert!(files.iter().all(|f| f.bytes <= limits.segment));
    assert!(files.iter().map(|f| f.bytes).sum::<u64>() <= limits.history);
}

#[cfg(unix)]
#[test]
fn symlink_ancestor_is_rejected_before_creating_external_directory() {
    use std::os::unix::fs::symlink;
    let root = Directory::new();
    let external = Directory::new();
    fs::create_dir_all(&root.0).unwrap();
    fs::create_dir_all(&external.0).unwrap();
    symlink(&external.0, root.0.join("redirect")).unwrap();
    let result = Journal::open(
        root.0.join("redirect").join("must-not-create"),
        "100-1000".into(),
        identity(),
        Limits::default(),
    );
    assert!(result.is_err());
    assert!(!external.0.join("must-not-create").exists());
}
