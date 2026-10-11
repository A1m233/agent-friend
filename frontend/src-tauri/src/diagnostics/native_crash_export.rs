//! Integrate immutable native-crash snapshots into the diagnostic archive.

use super::{json_entry, ArchiveEntry, ArchiveEntrySource, CollectionFailure};
use crate::native_crash::{ExportDump, NativeCrashExport};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::io::{Read, Write};

pub(super) fn append(
    result: Result<NativeCrashExport, ()>,
    include_dumps: bool,
    entries: &mut Vec<ArchiveEntry>,
    failures: &mut Vec<CollectionFailure>,
) -> Result<Vec<CollectionFailure>, String> {
    let export = match result {
        Ok(export) => export,
        Err(()) => {
            failures.push(CollectionFailure {
                collection: "native-crash".into(),
                item: None,
                message: "崩溃记录采集任务未完成（collector-task-failed）".into(),
            });
            entries.push(json_entry(
                "diagnostics/native-crash/coverage.json",
                &json!({
                    "schemaVersion": 1,
                    "platform": std::env::consts::OS,
                    "captureStatus": "unavailable",
                    "rawDumpsRequested": include_dumps,
                    "incidentCount": 0,
                    "rawDumpCount": 0,
                    "sessions": [],
                    "failures": ["collector-task-failed"],
                }),
            )?);
            return Ok(Vec::new());
        }
    };
    // This is an internal contract, but fail closed if a future collector bypasses consent.
    if export
        .coverage
        .get("rawDumpsRequested")
        .and_then(serde_json::Value::as_bool)
        != Some(include_dumps)
        || (!include_dumps && !export.dumps.is_empty())
    {
        return Err("native-crash dump export requires explicit consent".into());
    }
    entries.push(json_entry(
        "diagnostics/native-crash/coverage.json",
        &export.coverage,
    )?);
    for (name, incident) in export.incidents {
        entries.push(json_entry(&name, &incident)?);
    }
    for dump in export.dumps {
        entries.push(ArchiveEntry {
            name: dump.archive_name.clone(),
            source: ArchiveEntrySource::NativeCrashDump(dump),
        });
    }
    let mut warnings = Vec::new();
    for message in export.failures {
        // This code is persisted only when recovering an already-ended session.
        // Preserve it in coverage, but distinguish its evidence gap from export failure.
        if message == "observer-and-process-ended-without-result" {
            warnings.push(CollectionFailure {
                collection: "native-crash".into(),
                item: None,
                message: format!(
                    "历史运行的退出记录不完整，无法确定当时的退出原因；本次已导出保留的记录（{message}）"
                ),
            });
        } else {
            failures.push(CollectionFailure {
                collection: "native-crash".into(),
                item: None,
                message: format!("崩溃记录采集或导出不完整（{message}）"),
            });
        }
    }
    Ok(warnings)
}

pub(super) fn write_dump(mut dump: ExportDump, output: &mut impl Write) -> Result<(), String> {
    // Read the handle captured by the collector, never reopen a path subject to retention.
    // Recheck during streaming so mutation cannot make the ZIP contradict its metadata.
    let mut buffer = [0_u8; 64 * 1024];
    let mut hasher = Sha256::new();
    let mut copied = 0_u64;
    loop {
        let read = dump
            .file
            .read(&mut buffer)
            .map_err(|_| "native-crash dump read failed".to_string())?;
        if read == 0 {
            break;
        }
        copied = copied
            .checked_add(read as u64)
            .ok_or_else(|| "native-crash dump size overflow".to_string())?;
        if copied > dump.size_bytes {
            return Err("native-crash dump changed during export".into());
        }
        hasher.update(&buffer[..read]);
        output
            .write_all(&buffer[..read])
            .map_err(|_| "native-crash dump archive write failed".to_string())?;
    }
    if copied != dump.size_bytes || format!("{:x}", hasher.finalize()) != dump.sha256 {
        return Err("native-crash dump integrity changed during export".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{self, File};

    fn inspect_archive(archive: &std::path::Path) -> serde_json::Value {
        let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        let python = repo.join(if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python"
        });
        let mut command = if python.is_file() {
            std::process::Command::new(python)
        } else if cfg!(windows) {
            let mut command = std::process::Command::new("py");
            command.arg("-3");
            command
        } else {
            std::process::Command::new("python3")
        };
        let output =
            command
                .arg(repo.join(
                    ".cursor/skills/inspect-agent-friend-diagnostics/scripts/inspect_bundle.py",
                ))
                .arg(archive)
                .arg("--json")
                .env("PYTHONIOENCODING", "utf-8")
                .output()
                .expect("project Python interpreter");
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        serde_json::from_slice(&output.stdout).unwrap()
    }

    #[test]
    fn default_and_opt_in_producer_archives_are_readable_by_the_real_consumer() {
        use super::super::{
            build_bundle_manifest, contract, write_archive_atomically, BundleManifestInput,
        };
        let root = std::env::temp_dir().join(format!("af-crash-bundle-{}", rand::random::<u64>()));
        fs::create_dir_all(root.join("incidents")).unwrap();
        fs::create_dir_all(root.join("dumps")).unwrap();
        // This fixture exercises persisted collector/ZIP/consumer behavior, not OS capture.
        let dump = b"MDMPsynthetic private memory must never be printed";
        let id = "12-123-test-000001";
        fs::write(root.join("dumps").join(format!("{id}.dmp")), dump).unwrap();
        let incident = json!({
            "schemaVersion": 1, "incidentId": id, "sessionId": "12-123-test",
            "recordedAt": chrono::Utc::now().to_rfc3339(),
            "processId": 12, "processCreatedTicks": 123,
            "buildIdentity": null, "identityStatus": "development",
            "kind": "native-exception", "threadId": 42,
            "exceptionCode": "0xc000001d", "exceptionAddress": null,
            "faultModule": "agent-friend.exe", "faultRva": "0x1234",
            "exitCode": null, "exitTimeTicks": null, "orderlyReason": null,
            "reentrantCount": 0, "services": [],
            "dump": {"status": "captured", "sizeBytes": dump.len(),
                "sha256": format!("{:x}", Sha256::digest(dump)), "errorCode": null},
            "unknownPrivatePayload": "DO-NOT-EXPORT",
        });
        for (mode, include_dumps) in [
            ("formal", false),
            ("formal", true),
            ("local-test", false),
            ("local-test", true),
        ] {
            let mut installed_incident = incident.clone();
            installed_incident["identityStatus"] = json!("embedded");
            installed_incident["buildIdentity"] = json!({
                "schemaVersion": 1, "productVersion": "0.4.0", "buildId": "old-build-123",
                "sourceCommit": "a".repeat(40), "sourceTreeSha256": "b".repeat(64),
                "dirty": mode == "local-test", "buildMode": mode,
                "distribution": "ordinary", "profileId": "production",
            });
            fs::write(
                root.join("incidents").join(format!("{id}.json")),
                serde_json::to_vec(&installed_incident).unwrap(),
            )
            .unwrap();
            let export = crate::native_crash::export_from_test_dir(&root, include_dumps);
            assert!(export.failures.is_empty(), "{:?}", export.failures);
            assert_eq!(export.incidents[0].1["buildIdentity"]["buildMode"], mode);
            let mut entries = Vec::new();
            let mut failures = Vec::new();
            append(Ok(export), include_dumps, &mut entries, &mut failures).unwrap();
            assert!(failures.is_empty());
            let contract = contract::BundleContract::load().unwrap();
            let definition: serde_json::Value =
                serde_json::from_str(include_str!("bundle-contract-v1.json")).unwrap();
            for family in std::iter::once(&definition["core"])
                .chain(definition["capabilities"].as_array().unwrap().iter())
            {
                for entry in family["requiredEntries"].as_array().unwrap() {
                    let name = entry.as_str().unwrap();
                    if name != "manifest.json" && !entries.iter().any(|e| e.name == name) {
                        entries.push(json_entry(name, &json!({})).unwrap());
                    }
                }
            }
            let manifest = build_bundle_manifest(
                &contract,
                BundleManifestInput {
                    generated_at: "2026-10-08T00:00:00Z",
                    outcome: "complete",
                    requested_session_ids: &[],
                    included_session_ids: &[],
                    failures: &[],
                    warnings: &[],
                    logs: &[],
                    windows_shell: &json!({}),
                    attachments: &json!({}),
                    render_evidence: &json!({}),
                    pet_input_evidence: &json!({}),
                },
            );
            entries.push(json_entry("manifest.json", &manifest).unwrap());
            contract
                .validate_bundle(&manifest, entries.iter().map(|entry| entry.name.as_str()))
                .unwrap();
            let archive = write_archive_atomically(&root, "native-producer", entries).unwrap();
            let summary = inspect_archive(&archive);
            assert_eq!(summary["nativeCrashEvidence"]["evidence"], "collected");
            assert_eq!(summary["nativeCrashEvidence"]["incidentCount"], 1);
            assert_eq!(
                summary["nativeCrashEvidence"]["verifiedDumpCount"],
                u64::from(include_dumps)
            );
            assert_eq!(
                summary["nativeCrashEvidence"]["rawDumpsRequested"],
                include_dumps
            );
            assert_eq!(summary["nativeCrashEvidence"]["gaps"], json!([]));
            assert_eq!(
                summary["nativeCrashEvidence"]["builds"][0]["buildMode"],
                mode
            );
            assert_eq!(
                summary["nativeCrashEvidence"]["builds"][0]["sourceCommit"],
                "a".repeat(40)
            );
            assert_eq!(
                summary["nativeCrashEvidence"]["builds"][0]["dirty"],
                mode == "local-test"
            );
            assert!(!summary.to_string().contains("DO-NOT-EXPORT"));
            assert!(!summary.to_string().contains("synthetic private memory"));
            let mut zip = zip::ZipArchive::new(File::open(archive).unwrap()).unwrap();
            let mut metadata = String::new();
            zip.by_name(&format!("diagnostics/native-crash/incidents/{id}.json"))
                .unwrap()
                .read_to_string(&mut metadata)
                .unwrap();
            assert!(!metadata.contains("DO-NOT-EXPORT"));
            assert_eq!(
                zip.by_name(&format!("diagnostics/native-crash/dumps/{id}.dmp"))
                    .is_ok(),
                include_dumps
            );
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn historical_exit_gap_survives_export_without_masking_other_failures() {
        use super::super::{
            build_bundle_manifest, contract, write_archive_atomically, BundleManifestInput,
        };
        let root = std::env::temp_dir().join(format!("af-crash-history-{}", rand::random::<u64>()));
        fs::create_dir_all(root.join("sessions")).unwrap();
        fs::create_dir_all(root.join("incidents")).unwrap();
        fs::create_dir_all(root.join("dumps")).unwrap();
        let historical = "observer-and-process-ended-without-result";
        for extra_failure in [
            None,
            Some("watcher-handshake-failed"),
            Some("storage-unreadable"),
        ] {
            let mut codes = vec![historical];
            if let Some(code) = extra_failure {
                codes.push(code);
            }
            let session = json!({
                "schemaVersion": 1, "sessionId": "12-123-old",
                "processId": 12, "processCreatedTicks": 123,
                "recordedAt": "2026-10-10T00:00:00Z", "buildIdentity": null,
                "identityStatus": "development", "captureStatus": "ready",
                "watcherPid": null, "watcherCreatedTicks": null,
                "completed": true, "failures": codes,
            });
            let session_path = root.join("sessions/12-123-old.json");
            let original = serde_json::to_vec(&session).unwrap();
            fs::write(&session_path, &original).unwrap();
            let export = crate::native_crash::export_from_test_dir(&root, false);
            let mut entries = Vec::new();
            let mut failures = Vec::new();
            let warnings = append(Ok(export), false, &mut entries, &mut failures).unwrap();
            assert_eq!(warnings.len(), 1);
            assert!(warnings[0].message.contains(historical));
            assert_eq!(failures.len(), usize::from(extra_failure.is_some()));
            let outcome = if failures.is_empty() {
                "complete"
            } else {
                "partial"
            };
            let contract = contract::BundleContract::load().unwrap();
            let manifest = build_bundle_manifest(
                &contract,
                BundleManifestInput {
                    generated_at: "2026-10-11T00:00:00Z",
                    outcome,
                    requested_session_ids: &[],
                    included_session_ids: &[],
                    failures: &failures,
                    warnings: &warnings,
                    logs: &[],
                    windows_shell: &json!({}),
                    attachments: &json!({}),
                    render_evidence: &json!({}),
                    pet_input_evidence: &json!({}),
                },
            );
            let definition: serde_json::Value =
                serde_json::from_str(include_str!("bundle-contract-v1.json")).unwrap();
            for family in std::iter::once(&definition["core"])
                .chain(definition["capabilities"].as_array().unwrap().iter())
            {
                for entry in family["requiredEntries"].as_array().unwrap() {
                    let name = entry.as_str().unwrap();
                    if name != "manifest.json" && !entries.iter().any(|e| e.name == name) {
                        entries.push(json_entry(name, &json!({})).unwrap());
                    }
                }
            }
            entries.push(json_entry("manifest.json", &manifest).unwrap());
            contract
                .validate_bundle(&manifest, entries.iter().map(|e| e.name.as_str()))
                .unwrap();
            let archive = write_archive_atomically(&root, "history", entries).unwrap();
            let summary = inspect_archive(&archive);
            assert_eq!(summary["manifest"]["outcome"], outcome);
            assert_eq!(summary["manifest"]["warnings"].as_array().unwrap().len(), 1);
            assert_eq!(summary["nativeCrashEvidence"]["historicalGapCount"], 1);
            assert_eq!(
                summary["nativeCrashEvidence"]["failureCount"],
                usize::from(extra_failure.is_some())
            );
            assert_eq!(summary["nativeCrashEvidence"]["evidence"], "partial");
            let mut zip = zip::ZipArchive::new(File::open(archive).unwrap()).unwrap();
            let mut coverage = String::new();
            zip.by_name("diagnostics/native-crash/coverage.json")
                .unwrap()
                .read_to_string(&mut coverage)
                .unwrap();
            let coverage: serde_json::Value = serde_json::from_str(&coverage).unwrap();
            assert_eq!(coverage["sessions"][0]["failures"], session["failures"]);
            assert_eq!(fs::read(&session_path).unwrap(), original);
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn streamed_dump_detects_changed_content_and_size() {
        let root = std::env::temp_dir().join(format!("af-dump-export-{}", rand::random::<u64>()));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("fixture.dmp");
        let original = vec![42_u8; 150_000];
        fs::write(&path, &original).unwrap();
        let dump = || ExportDump {
            file: File::open(&path).unwrap(),
            archive_name: "diagnostics/native-crash/dumps/fixture.dmp".into(),
            size_bytes: original.len() as u64,
            sha256: format!("{:x}", Sha256::digest(&original)),
        };
        let mut output = Vec::new();
        write_dump(dump(), &mut output).unwrap();
        assert_eq!(output, original);
        fs::write(&path, vec![41_u8; original.len()]).unwrap();
        assert!(write_dump(dump(), &mut Vec::new()).is_err());
        fs::write(&path, b"truncated").unwrap();
        assert!(write_dump(dump(), &mut Vec::new()).is_err());
        fs::write(&path, vec![42_u8; original.len() + 1]).unwrap();
        assert!(write_dump(dump(), &mut Vec::new()).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn collection_failure_keeps_coverage_and_marks_the_bundle_partial() {
        let mut entries = Vec::new();
        let mut failures = Vec::new();
        append(Err(()), false, &mut entries, &mut failures).unwrap();
        assert_eq!(failures.len(), 1);
        assert_eq!(entries[0].name, "diagnostics/native-crash/coverage.json");
        let ArchiveEntrySource::Bytes(body) = &entries[0].source else {
            panic!("coverage must be structured metadata");
        };
        let value: serde_json::Value = serde_json::from_slice(body).unwrap();
        assert_eq!(value["rawDumpsRequested"], false);
        assert_eq!(value["failures"], json!(["collector-task-failed"]));
    }

    #[test]
    fn bundle_request_does_not_inherit_raw_dump_consent() {
        let request: super::super::GenerateDiagnosticBundleRequest =
            serde_json::from_value(json!({"sessionIds": [], "includeAttachmentOriginals": true}))
                .unwrap();
        assert!(!request.include_crash_dumps);
        let explicit: super::super::GenerateDiagnosticBundleRequest =
            serde_json::from_value(json!({"sessionIds": [], "includeCrashDumps": true})).unwrap();
        assert!(explicit.include_crash_dumps);
    }

    #[test]
    fn collector_cannot_change_the_requests_dump_consent() {
        let export = NativeCrashExport {
            coverage: json!({"rawDumpsRequested": true}),
            incidents: vec![],
            dumps: vec![],
            failures: vec![],
        };
        assert!(append(Ok(export), false, &mut Vec::new(), &mut Vec::new()).is_err());
    }

    #[test]
    fn setup_failure_is_propagated_into_the_bundles_partial_failures() {
        let export = NativeCrashExport {
            coverage: json!({"rawDumpsRequested": false, "failures": ["watcher-handshake-failed"]}),
            incidents: vec![],
            dumps: vec![],
            failures: vec!["watcher-handshake-failed".into()],
        };
        let mut failures = Vec::new();
        append(Ok(export), false, &mut Vec::new(), &mut failures).unwrap();
        assert_eq!(failures.len(), 1);
        assert_eq!(failures[0].collection, "native-crash");
        assert!(failures[0].message.contains("崩溃记录采集或导出不完整"));
        assert!(failures[0].message.contains("watcher-handshake-failed"));
    }
}
