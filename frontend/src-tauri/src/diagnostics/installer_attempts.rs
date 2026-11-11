use super::{json_entry, ArchiveEntry, ArchiveEntrySource, CollectionFailure};
use chrono::{DateTime, Utc};
use serde_json::{json, Value};
use std::{
    fs::{self, OpenOptions},
    io::{self, Read},
    path::Path,
};

const LIMIT: usize = 5;
const MAX_BYTES: u64 = 256 * 1024;
const INDEX: &str = "install/attempts/index.json";

fn file_identity<'a>(name: &'a str, prefix: &str) -> Option<(&'a str, Option<DateTime<Utc>>)> {
    let stem = name.strip_prefix(prefix)?.strip_suffix(".log")?;
    let (build, start) = match stem.split_once("--") {
        Some((build, attempt)) => {
            let (ticks, pid) = attempt.split_once('-')?;
            pid.parse::<u32>().ok()?;
            let unix_ticks = ticks.parse::<i64>().ok()?.checked_sub(116444736000000000)?;
            let start = DateTime::from_timestamp(
                unix_ticks.div_euclid(10000000),
                (unix_ticks.rem_euclid(10000000) * 100) as u32,
            )?;
            (build, Some(start))
        }
        None => (stem, None),
    };
    let mut fields = build.split('-');
    let date = fields.next()?;
    chrono::NaiveDateTime::parse_from_str(date, "%Y%m%dT%H%M%SZ").ok()?;
    for len in [12, 8] {
        let hex = fields.next()?;
        if hex.len() != len || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
    }
    fields.next().is_none().then_some((build, start))
}

fn read_log(path: &Path) -> io::Result<String> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
    let file = options.open(path)?;
    let meta = file.metadata()?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return Err(io::Error::other("reparse point"));
        }
    }
    if !meta.is_file() || meta.len() > MAX_BYTES {
        return Err(io::Error::other("unsupported installer log"));
    }
    let mut bytes = Vec::new();
    file.take(meta.len()).read_to_end(&mut bytes)?;
    let data = bytes
        .strip_prefix(&[0xff, 0xfe])
        .ok_or_else(|| io::Error::other("encoding"))?;
    let words: Vec<_> = data
        .chunks_exact(2)
        .map(|b| u16::from_le_bytes([b[0], b[1]]))
        .collect();
    Ok(String::from_utf16_lossy(&words))
}

fn known_phase(value: &str) -> bool {
    matches!(
        value,
        "attempt"
            | "environment"
            | "webview2"
            | "prepare-helper"
            | "install-checks"
            | "preinstall"
            | "process-check"
            | "copy-main"
            | "extract-resources"
            | "register"
            | "verify"
            | "credentials"
            | "activate"
            | "cleanup"
            | "retire-source"
            | "restore-shortcuts"
            | "commit"
            | "rollback"
    )
}

fn known_result(value: &str) -> bool {
    matches!(value, "ok" | "failed" | "cancelled" | "error" | "timeout")
        || value.parse::<i32>().is_ok()
}

// Only project structured installer fields. Old logs may include arbitrary helper
// stderr and absolute paths. Neither belongs in the exported attempt history.
fn project_log(
    text: &str,
    product: &str,
    profile: &str,
    build: &str,
    legacy: bool,
) -> Option<(Value, String)> {
    // An in-flight or interrupted UTF-16 write can leave a partial final line.
    let complete = text
        .rsplit_once('\n')
        .map(|(complete, _)| complete)
        .unwrap_or("");
    let lines = complete
        .split_terminator('\n')
        .filter_map(|l| l.trim_end_matches('\r').split_once('='))
        .collect::<Vec<_>>();
    let field = |key: &str| lines.iter().find(|(k, _)| *k == key).map(|(_, v)| *v);
    if field("product") != Some(product)
        || field("profileId") != Some(profile)
        || field("buildId") != Some(build)
        || field("schemaVersion") != Some(if legacy { "2" } else { "3" })
    {
        return None;
    }
    let mut log = String::new();
    let mut last = None;
    let mut status = if legacy { "legacy" } else { "no-end-record" };
    for &(key, value) in &lines {
        match key {
            "schemaVersion" | "product" | "buildId" | "profileId" if field(key) == Some(value) => {}
            "version"
                if value.len() <= 80
                    && value
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b".-+".contains(&b)) => {}
            "installKind" if matches!(value, "first-install" | "in-place" | "explicit-move") => {}
            "event" if !legacy => {
                let parts = value.split('|').collect::<Vec<_>>();
                let (time, phase, result) = match parts.as_slice() {
                    [t, p, "start"] => (*t, *p, None),
                    [t, p, "end", r] if known_result(r) => (*t, *p, Some(*r)),
                    _ => continue,
                };
                if !known_phase(phase) || DateTime::parse_from_rfc3339(time).is_err() {
                    continue;
                }
                if phase != "attempt" {
                    last = Some(phase);
                } else if let Some(result) = result {
                    status = result;
                }
            }
            "phase" if known_phase(value) => {
                if legacy {
                    last = Some(value);
                }
            }
            "helperResult" if known_result(value) => {}
            "helperPresentAfterRun" if matches!(value, "0" | "1") => {}
            "helperOutput" => {
                if let Ok(output) = serde_json::from_str::<Value>(value) {
                    let mut safe = json!({"ok":output.get("ok").and_then(Value::as_bool)});
                    for name in ["recovered", "terminatedPids", "cleaned", "pending"] {
                        if let Some(items) = output[name].as_array() {
                            safe[format!("{name}Count")] = json!(items.len());
                        }
                    }
                    for name in ["buildIdentitySha256", "payloadManifestSha256"] {
                        if let Some(hash) = output[name]
                            .as_str()
                            .filter(|h| h.len() == 64 && h.bytes().all(|b| b.is_ascii_hexdigit()))
                        {
                            safe[name] = json!(hash);
                        }
                    }
                    if let Some(error) = output["error"].as_str() {
                        let code = error.split(':').next().unwrap_or("");
                        if code.len() <= 80
                            && code.contains('_')
                            && code.bytes().all(|c| c.is_ascii_lowercase() || c == b'_')
                        {
                            safe["errorCode"] = json!(code);
                        }
                    }
                    log.push_str(&format!("helperOutput={safe}\n"));
                }
                continue;
            }
            _ => continue,
        }
        log.push_str(key);
        log.push('=');
        log.push_str(value);
        log.push('\n');
    }
    let summary = json!({"buildId":build,"legacy":legacy,"lastPhase":last,"status":status});
    Some((summary, log))
}

pub(super) fn collect(
    dir: &Path,
    product: &str,
    profile: &str,
    supported: bool,
    entries: &mut Vec<ArchiveEntry>,
    failures: &mut Vec<CollectionFailure>,
) -> Result<(), String> {
    let mut candidates = Vec::new();
    let mut gaps = 0;
    if supported {
        let prefix = format!("{product}-installer-");
        match fs::read_dir(dir) {
            Ok(files) => {
                for file in files.flatten() {
                    let name = file.file_name();
                    let Some((build, start)) =
                        name.to_str().and_then(|n| file_identity(n, &prefix))
                    else {
                        continue;
                    };
                    let Ok(meta) = file.metadata() else {
                        gaps += 1;
                        continue;
                    };
                    if !meta.is_file() || meta.is_symlink() {
                        continue;
                    }
                    let time = start.or_else(|| meta.modified().ok().map(DateTime::<Utc>::from));
                    if let Some(time) = time {
                        candidates.push((time, file.path(), build.to_owned(), start.is_none()));
                    }
                }
            }
            Err(_) => gaps += 1,
        }
    }
    candidates.sort_unstable_by(|a, b| b.cmp(a));
    let mut attempts = Vec::new();
    for (time, path, build, legacy) in candidates {
        let Some((mut item, report)) = read_log(&path)
            .ok()
            .and_then(|t| project_log(&t, product, profile, &build, legacy))
        else {
            gaps += 1;
            continue;
        };
        let entry = format!("install/attempts/attempt-{:02}.log", attempts.len() + 1);
        entries.push(ArchiveEntry {
            name: entry.clone(),
            source: ArchiveEntrySource::Bytes(report.into_bytes()),
        });
        item["entry"] = json!(entry);
        item["time"] = json!(time.to_rfc3339());
        attempts.push(item);
        if attempts.len() == LIMIT {
            break;
        }
    }
    if gaps > 0 {
        failures.push(CollectionFailure {
            collection: "installer-attempts".into(),
            item: None,
            message: format!("{gaps} installer log sources could not be read"),
        });
    }
    entries.push(json_entry(
        INDEX,
        &json!({"supported":supported,"attempts":attempts,"gaps":gaps}),
    )?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnostics::write_archive_atomically;
    use std::fs::File;

    #[test]
    fn retry_and_unfinished_attempt_survive_in_same_zip_with_legacy_logs() {
        const BUILD: &str = "20261111T020127Z-6199524379e3-469833c8";
        let dir =
            std::env::temp_dir().join(format!("installer-log-test-{}", rand::random::<u64>()));
        fs::create_dir(&dir).unwrap();
        let write = |suffix: &str, schema, body: &str| {
            let path = dir.join(format!("agent-friend-installer-{BUILD}{suffix}.log"));
            let log = format!("\u{feff}schemaVersion={schema}\r\nproduct=agent-friend\r\nprofileId=production\r\nbuildId={BUILD}\r\n{body}");
            fs::write(
                path,
                log.encode_utf16()
                    .flat_map(u16::to_le_bytes)
                    .collect::<Vec<_>>(),
            )
            .unwrap();
        };
        // Old attempts have earlier timestamps than this legacy file's modification time.
        for i in 0..5 {
            write(&format!("--13300000000000000{i}-10"), 3, "");
        }
        write(
            "",
            2,
            "phase=verify\r\nhelperResult=0\r\ninstallerPath=PRIVATE\r\n",
        );
        let legacy_path = dir.join(format!("agent-friend-installer-{BUILD}.log"));
        File::options()
            .write(true)
            .open(legacy_path)
            .unwrap()
            .set_modified(std::time::UNIX_EPOCH + std::time::Duration::from_secs(1_800_000_000))
            .unwrap();
        let event = "event=2026-11-11T02:01:27.123Z|";
        write(
            "--140000000000000000-20",
            3,
            &format!("{event}copy-main|start\r\n{event}copy-main|end|0"),
        );
        write(
            "--140000000000010000-21",
            3,
            &format!("{event}commit|end|0\r\n{event}attempt|end|ok\r\n"),
        );
        let mut entries = Vec::new();
        let mut failures = Vec::new();
        collect(
            &dir,
            "agent-friend",
            "production",
            true,
            &mut entries,
            &mut failures,
        )
        .unwrap();
        assert!(failures.is_empty());
        let zip_path = write_archive_atomically(&dir, "installer-log-test", entries).unwrap();
        let mut zip = zip::ZipArchive::new(File::open(zip_path).unwrap()).unwrap();
        let index: Value = serde_json::from_reader(zip.by_name(INDEX).unwrap()).unwrap();
        let mut read = |name: &str| {
            let mut log = String::new();
            zip.by_name(name).unwrap().read_to_string(&mut log).unwrap();
            log
        };
        let attempts = index["attempts"].as_array().unwrap();
        assert_eq!(attempts.len(), 5);
        assert_eq!(attempts[0]["status"], "ok");
        assert_eq!(attempts[1]["status"], "no-end-record");
        assert_eq!(attempts[1]["lastPhase"], "copy-main");
        let partial = read(attempts[1]["entry"].as_str().unwrap());
        assert!(partial.contains("2026-11-11T02:01:27.123Z|copy-main|start"));
        assert!(!partial.contains("|end|"));
        let legacy = read(attempts[2]["entry"].as_str().unwrap());
        assert!(legacy.contains("phase=verify\nhelperResult=0\n"));
        assert!(!legacy.contains("PRIVATE"));
        drop(zip);
        fs::remove_dir_all(dir).unwrap();
    }
}
