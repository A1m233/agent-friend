use super::model::{Record, CONTEXT_LIMIT, HISTORY_LIMIT, INCIDENT_FILE_LIMIT, INCIDENT_LIMIT};
use serde_json::{json, Value};
use std::{
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
};

pub struct Journal {
    pub directory: PathBuf,
    file: File,
    bytes: u64,
    segment: u64,
    prefix: i64,
    pub evicted: u64,
}
pub fn files(directory: &Path, prefix: &str) -> std::io::Result<Vec<(PathBuf, u64)>> {
    let mut result = Vec::new();
    if !directory.exists() {
        return Ok(result);
    }
    for (index, entry) in fs::read_dir(directory)?.enumerate() {
        if index >= 512 {
            return Err(std::io::Error::other("directory-scan-limit"));
        }
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let meta = entry.metadata()?;
        if entry.file_type()?.is_symlink()
            || !meta.is_file()
            || !name.starts_with(prefix)
            || !(name.ends_with(".jsonl") || name.ends_with(".json"))
        {
            continue;
        }
        result.push((entry.path(), meta.len()));
    }
    result.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(result)
}
fn prune(
    directory: &Path,
    prefix: &str,
    budget: u64,
    count: usize,
    age_days: i64,
    now: i64,
) -> std::io::Result<u64> {
    let items = files(directory, prefix)?;
    let mut bytes: u64 = items.iter().map(|i| i.1).sum();
    let mut remaining = items.len();
    let mut removed = 0;
    for (path, size) in items {
        let at = path
            .file_name()
            .and_then(|s| s.to_str())
            .and_then(|s| s.strip_prefix(prefix))
            .and_then(|s| s.split(['-', '.']).next())
            .and_then(|s| s.parse::<i64>().ok());
        let expired = at.is_some_and(|at| now.saturating_sub(at) > age_days * 86_400_000);
        if expired || bytes > budget || remaining > count {
            fs::remove_file(path)?;
            bytes = bytes.saturating_sub(size);
            remaining -= 1;
            removed += 1;
        }
    }
    Ok(removed)
}
impl Journal {
    pub fn open(directory: PathBuf, now: i64) -> std::io::Result<Self> {
        fs::create_dir_all(&directory)?;
        let file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(directory.join(format!("history-{now}-{}-000000.jsonl", std::process::id())))?;
        let mut j = Self {
            directory,
            file,
            bytes: 0,
            segment: 0,
            prefix: now,
            evicted: 0,
        };
        j.prune(now)?;
        Ok(j)
    }
    pub fn append(&mut self, record: &Record) -> std::io::Result<()> {
        let mut b = serde_json::to_vec(record)?;
        b.push(b'\n');
        if self.bytes + b.len() as u64 > 1024 * 1024 {
            self.file.sync_data()?;
            self.segment += 1;
            self.file = OpenOptions::new().create_new(true).write(true).open(
                self.directory
                    .join(format!("history-{}-{:06}.jsonl", self.prefix, self.segment)),
            )?;
            self.bytes = 0;
            self.prune(record.recorded_at)?;
        }
        self.file.write_all(&b)?;
        self.bytes += b.len() as u64;
        Ok(())
    }
    pub fn sync(&mut self) -> std::io::Result<()> {
        self.file.sync_data()
    }
    pub fn prune(&mut self, now: i64) -> std::io::Result<()> {
        self.evicted += prune(
            &self.directory,
            "history-",
            HISTORY_LIMIT - 1024 * 1024,
            64,
            7,
            now,
        )?;
        self.evicted += prune(&self.directory, "incident-", INCIDENT_LIMIT, 32, 30, now)?;
        Ok(())
    }
    pub fn incident(
        &mut self,
        id: &str,
        signal: &str,
        records: &[Record],
        complete: bool,
        omitted: u64,
    ) -> std::io::Result<()> {
        let value = json!({"schemaVersion":1,"incidentId":id,"signal":signal,"postWindowComplete":complete && omitted==0,"omittedRecords":omitted,
            "presentation":"unknown","records":records});
        let data = serde_json::to_vec(&value)?;
        if data.len() > INCIDENT_FILE_LIMIT {
            return Err(std::io::Error::other("incident-size-limit"));
        }
        atomic_write(&self.directory.join(format!("incident-{id}.json")), &data)?;
        self.prune(chrono::Utc::now().timestamp_millis())
    }
}
pub fn atomic_write(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let temp = path.with_extension("part");
    if path.is_symlink() || temp.is_symlink() {
        return Err(std::io::Error::other("linked-output"));
    }
    let result = (|| {
        let mut f = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&temp)?;
        f.write_all(data)?;
        f.sync_all()?;
        drop(f);
        fs::rename(&temp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

/// Read bounded persisted context, including a previous process; never ask a page for it.
pub fn recent(directory: &Path) -> std::io::Result<Vec<Record>> {
    let mut rows = std::collections::VecDeque::new();
    let mut bytes = 0;
    let items = files(directory, "history-")?;
    // Only the two newest segments can contribute to a 256 KiB tail.
    for (path, _) in items
        .into_iter()
        .rev()
        .take(2)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
    {
        let f = File::open(path)?;
        for line in BufReader::new(std::io::Read::take(f, 1024 * 1024)).lines() {
            let line = line?;
            if line.len() > 16 * 1024 {
                continue;
            }
            if let Ok(row) = serde_json::from_str::<Record>(&line) {
                if !row.valid() {
                    return Err(std::io::Error::other("invalid-record"));
                }
                bytes += line.len();
                rows.push_back((line.len(), row));
                while bytes > CONTEXT_LIMIT {
                    if let Some((size, _)) = rows.pop_front() {
                        bytes -= size;
                    }
                }
            }
        }
    }
    Ok(rows.into_iter().map(|(_, r)| r).collect())
}

pub struct Export {
    pub entries: Vec<(String, Vec<u8>)>,
    pub coverage: Value,
    pub failures: Vec<String>,
}
impl Export {
    pub fn failed() -> Self {
        Self {
            entries: vec![],
            coverage: json!({"schemaVersion":1,"artifacts":[],"gaps":["read-failed"],
            "presentation":"unknown","tailCompleteness":"unknown"}),
            failures: vec!["read-failed".into()],
        }
    }
}
pub fn export(directory: &Path) -> Export {
    let mut out = Export {
        entries: vec![],
        coverage: json!({"schemaVersion":1,"supported":cfg!(target_os="windows"),
        "presentation":"unknown","durability":"persisted-only","tailCompleteness":"unknown","artifacts":[],"gaps":[]}),
        failures: vec![],
    };
    let mut artifacts = vec![];
    let mut gaps = vec![];
    for (prefix, limit) in [("history-", HISTORY_LIMIT), ("incident-", INCIDENT_LIMIT)] {
        let items = match files(directory, prefix) {
            Ok(v) => v,
            Err(_) => {
                out.failures.push("read-failed".into());
                continue;
            }
        };
        let mut used = 0;
        for (i, (path, size)) in items.into_iter().rev().enumerate() {
            let file_limit = if prefix == "history-" {
                1024 * 1024
            } else {
                INCIDENT_FILE_LIMIT as u64
            };
            if size > file_limit || used + size > limit {
                gaps.push("truncated");
                continue;
            }
            used += size;
            let mut data = match File::open(&path).and_then(|f| {
                let mut b = Vec::new();
                std::io::Read::read_to_end(&mut std::io::Read::take(f, size), &mut b)?;
                Ok(b)
            }) {
                Ok(b) => b,
                Err(_) => {
                    out.failures.push("read-failed".into());
                    continue;
                }
            };
            let ext = if prefix == "history-" {
                "jsonl"
            } else {
                "json"
            };
            let name = format!("diagnostics/pet-render/{prefix}{:03}.{ext}", i + 1);
            if ext == "jsonl" && !data.ends_with(b"\n") {
                gaps.push("incomplete-tail");
            }
            let valid = if ext == "jsonl" {
                let mut safe = Vec::new();
                for line in data.split_inclusive(|b| *b == b'\n') {
                    if !line.ends_with(b"\n") {
                        continue;
                    }
                    if serde_json::from_slice::<Record>(line).is_ok_and(|r| r.valid()) {
                        safe.extend_from_slice(line);
                    } else {
                        gaps.push("invalid");
                        out.failures.push("invalid-record".into());
                    }
                }
                data = safe;
                !data.is_empty()
            } else {
                serde_json::from_slice::<Value>(&data).is_ok_and(|v| {
                    v.as_object().is_some_and(|o| {
                        o.keys().all(|k| {
                            [
                                "schemaVersion",
                                "incidentId",
                                "signal",
                                "postWindowComplete",
                                "omittedRecords",
                                "presentation",
                                "records",
                            ]
                            .contains(&k.as_str())
                        })
                    }) && v["schemaVersion"] == 1
                        && v["presentation"] == "unknown"
                        && v["postWindowComplete"].is_boolean()
                        && v["incidentId"].as_str().is_some_and(|s| {
                            s.len() <= 96 && s.bytes().all(|b| b.is_ascii_digit() || b == b'-')
                        })
                        && v["signal"].as_str().is_some_and(|s| {
                            [
                                "user-reported",
                                "context-lost",
                                "observer-failed",
                                "render-progress-missing",
                                "observation-gap",
                                "window-operation-failed",
                            ]
                            .contains(&s)
                        })
                        && v["records"].as_array().is_some_and(|a| {
                            a.iter().all(|r| {
                                serde_json::from_value::<Record>(r.clone()).is_ok_and(|r| r.valid())
                            })
                        })
                })
            };
            if !valid {
                out.failures.push("invalid-record".into());
                gaps.push("invalid");
                continue;
            }
            artifacts.push(json!({"entry":name,"bytes":data.len()}));
            out.entries.push((name, data));
        }
    }
    if artifacts.is_empty() {
        gaps.push("not-covered");
    }
    match read_status(directory) {
        Ok(b) if b.len() <= 16 * 1024 => {
            if let Ok(v) = serde_json::from_slice::<Value>(&b) {
                // Copy only scalar counts, not arbitrary values from disk.
                for key in ["dropped", "invalid", "writeFailed", "evicted"] {
                    if let Some(n) = v[key].as_u64() {
                        out.coverage[key] = n.into();
                        if n > 0 {
                            gaps.push(key);
                        }
                    }
                }
                out.coverage["normalEnd"] = v["normalEnd"].as_bool().into();
            } else {
                gaps.push("corrupt-status");
            }
        }
        _ => gaps.push("status-unavailable"),
    }
    out.coverage["artifacts"] = artifacts.into();
    out.coverage["gaps"] = json!(gaps);
    out
}

pub fn read_status(directory: &Path) -> std::io::Result<Vec<u8>> {
    let path = directory.join("status.json");
    if fs::symlink_metadata(&path)?.file_type().is_symlink() {
        return Err(std::io::Error::other("linked-status"));
    }
    let f = File::open(path)?;
    if f.metadata()?.len() > 16 * 1024 {
        return Err(std::io::Error::other("oversized-status"));
    }
    let mut bytes = Vec::new();
    std::io::Read::read_to_end(&mut std::io::Read::take(f, 16 * 1024), &mut bytes)?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn directory() -> PathBuf {
        std::env::temp_dir().join(format!("render-journal-{}", rand::random::<u64>()))
    }
    fn row(seq: u64) -> Record {
        Record {
            schema_version: 1,
            process_session_id: "test-process".into(),
            identity: json!({"buildMode":"debug"}),
            record_seq: seq,
            recorded_at: chrono::Utc::now().timestamp_millis(),
            monotonic_ms: seq,
            event: json!({"kind":"process-start"}),
        }
    }
    #[test]
    fn healthy_rotation_does_not_replace_incident_and_torn_tail_preserves_valid_prefix() {
        let dir = directory();
        let now = chrono::Utc::now().timestamp_millis();
        let mut j = Journal::open(dir.clone(), now).unwrap();
        j.append(&row(1)).unwrap();
        j.incident(&format!("{now}-1"), "user-reported", &[row(1)], false, 0)
            .unwrap();
        // Exercise pruning with sparse, disposable history segments.
        for i in 1..20 {
            let f = File::create(dir.join(format!("history-{now}-old-{i:06}.jsonl"))).unwrap();
            f.set_len(1024 * 1024).unwrap();
        }
        j.prune(now).unwrap();
        assert!(
            files(&dir, "history-")
                .unwrap()
                .iter()
                .map(|r| r.1)
                .sum::<u64>()
                <= HISTORY_LIMIT
        );
        assert_eq!(files(&dir, "incident-").unwrap().len(), 1);
        drop(j);
        let path = dir.join(format!("history-{}-latest.jsonl", now + 1));
        let mut bytes = serde_json::to_vec(&row(2)).unwrap();
        bytes.extend_from_slice(b"\n{\"partial\"");
        fs::write(path, bytes).unwrap();
        let out = export(&dir);
        assert!(out.coverage["gaps"]
            .as_array()
            .unwrap()
            .contains(&json!("incomplete-tail")));
        assert!(out
            .entries
            .iter()
            .any(|(n, b)| n.contains("history")
                && String::from_utf8_lossy(b).contains("test-process")));
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn untrusted_fields_do_not_leave_disk_and_oversized_status_is_bounded() {
        let dir = directory();
        fs::create_dir_all(&dir).unwrap();
        let mut v = serde_json::to_value(row(1)).unwrap();
        v["event"]["secret"] = json!("DO_NOT_EXPORT");
        fs::write(dir.join("history-1-1.jsonl"), format!("{v}\n")).unwrap();
        let f = File::create(dir.join("status.json")).unwrap();
        f.set_len(1024 * 1024).unwrap();
        drop(f);
        let out = export(&dir);
        assert!(!out.failures.is_empty());
        assert!(out
            .entries
            .iter()
            .all(|(_, b)| !String::from_utf8_lossy(b).contains("DO_NOT_EXPORT")));
        assert!(read_status(&dir).is_err());
        fs::remove_dir_all(dir).unwrap();
    }
}
