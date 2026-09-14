//! Persisted sandbox failures remain exportable when the bridge is unavailable.
use serde_json::{json, Value};
use std::{fs, io::Read, path::Path};

const MAX_ITEM_BYTES: u64 = 128 * 1024;
const MAX_ITEMS: usize = 32;

pub(super) fn collect(log_dir: &Path) -> Value {
    let directory = log_dir.join("command-runtime-incidents");
    let mut gaps = vec!["bridge_unavailable"];
    let mut incidents = Vec::new();
    if let Ok(file) = fs::File::open(directory.join("coverage")) {
        let mut bytes = Vec::new();
        if file.take(4097).read_to_end(&mut bytes).is_ok() && bytes.len() <= 4096 {
            if let Ok(value) = serde_json::from_slice::<Value>(&bytes) {
                for code in [
                    "write_failed",
                    "retention_evicted",
                    "incident_oversized",
                    "invalid_incident",
                    "history_unavailable",
                ] {
                    if value["gaps"]
                        .as_array()
                        .is_some_and(|items| items.contains(&json!(code)))
                    {
                        gaps.push(code);
                    }
                }
            } else {
                gaps.push("coverage_invalid");
            }
        } else {
            gaps.push("coverage_unavailable");
        }
    }
    match fs::read_dir(&directory) {
        Ok(entries) => {
            let mut paths = entries
                .filter_map(Result::ok)
                .filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some("json"))
                .collect::<Vec<_>>();
            paths.sort_by_key(|entry| {
                std::cmp::Reverse(entry.metadata().ok().and_then(|m| m.modified().ok()))
            });
            if paths.len() > MAX_ITEMS {
                gaps.push("retention_limited");
            }
            for entry in paths.iter().take(MAX_ITEMS) {
                let path = entry.path();
                if path.extension().and_then(|x| x.to_str()) != Some("json") {
                    continue;
                }
                let Some(info) = fs::symlink_metadata(&path).ok() else {
                    gaps.push("read_failed");
                    continue;
                };
                if info
                    .modified()
                    .ok()
                    .and_then(|t| t.elapsed().ok())
                    .is_some_and(|age| age.as_secs() > 30 * 86400)
                {
                    gaps.push("retention_evicted");
                    continue;
                }
                if !info.is_file() || info.file_type().is_symlink() || info.len() > MAX_ITEM_BYTES {
                    gaps.push("invalid_incident");
                    continue;
                }
                let value = (|| {
                    let mut bytes = Vec::new();
                    fs::File::open(&path)?
                        .take(MAX_ITEM_BYTES + 1)
                        .read_to_end(&mut bytes)?;
                    if bytes.len() as u64 > MAX_ITEM_BYTES {
                        return Err(std::io::ErrorKind::InvalidData.into());
                    }
                    serde_json::from_slice::<Value>(&bytes).map_err(std::io::Error::other)
                })();
                match value {
                    Ok(value)
                        if value.get("schemaVersion") == Some(&json!(1))
                            && value.get("id").is_some_and(Value::is_string) =>
                    {
                        incidents.push(value)
                    }
                    _ => gaps.push("invalid_incident"),
                }
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => gaps.push("not_collected"),
        Err(_) => gaps.push("history_unavailable"),
    }
    gaps.sort_unstable();
    gaps.dedup();
    json!({"schemaVersion": 1, "incidents": incidents, "gaps": gaps,
           "scope": "persisted-only", "limits": {"count": MAX_ITEMS, "itemBytes": MAX_ITEM_BYTES, "ageDays": 30}})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offline_export_retains_failure_after_repair_and_bounds_bad_files() {
        let temp = std::env::temp_dir().join(format!(
            "agent-friend-incident-test-{}",
            rand::random::<u64>()
        ));
        let directory = temp.join("command-runtime-incidents");
        fs::create_dir_all(&directory).unwrap();
        for (id, outcome) in [("failure", "failed"), ("repair", "ok")] {
            fs::write(
                directory.join(format!("{id}.json")),
                serde_json::to_vec(&json!({"schemaVersion":1,"id":id,"outcome":outcome})).unwrap(),
            )
            .unwrap();
        }
        fs::write(directory.join("broken.json"), b"{").unwrap();
        fs::write(
            directory.join("coverage"),
            br#"{"gaps":["retention_evicted"]}"#,
        )
        .unwrap();
        let value = collect(&temp);
        assert_eq!(value["incidents"].as_array().unwrap().len(), 2);
        assert!(value["gaps"]
            .as_array()
            .unwrap()
            .contains(&json!("invalid_incident")));
        assert!(value["gaps"]
            .as_array()
            .unwrap()
            .contains(&json!("bridge_unavailable")));
        assert!(value["gaps"]
            .as_array()
            .unwrap()
            .contains(&json!("retention_evicted")));
        fs::remove_dir_all(temp).unwrap();
    }
}
