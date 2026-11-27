use super::{job::Control, scratch::Scratch, AttachmentCandidate};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    io::{BufWriter, Write},
    path::Path,
    time::Duration,
};

// Reset for each chunk: a large, progressing export has no total deadline.
const SOURCE_IDLE_TIMEOUT: Duration = Duration::from_secs(30);

async fn wait_source<F: std::future::Future>(
    control: &Control,
    future: F,
    timeout: Duration,
) -> Result<F::Output, String> {
    control
        .wait(tokio::time::timeout(timeout, future))
        .await?
        .map_err(|_| "session source stopped responding".into())
}

pub(super) struct SessionExport {
    pub file: Scratch,
    pub complete: bool,
}

pub(super) async fn collect(
    client: &reqwest::Client,
    mut url: reqwest::Url,
    session_id: &str,
    turns: Option<u32>,
    credentials: &[String],
    home: Option<&Path>,
    control: &Control,
    attachments: &mut BTreeMap<String, AttachmentCandidate>,
) -> Result<SessionExport, String> {
    url.path_segments_mut()
        .map_err(|_| "invalid bridge URL")?
        .extend(["v1", "sessions", session_id, "diagnostics", "stream"]);
    match turns {
        Some(n) => {
            url.query_pairs_mut()
                .append_pair("turn_limit", &n.to_string());
        }
        None => {
            url.query_pairs_mut().append_pair("all_turns", "true");
        }
    }
    let mut response = wait_source(control, client.get(url).send(), SOURCE_IDLE_TIMEOUT)
        .await?
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("bridge returned HTTP {}", response.status()));
    }
    let mut file = Scratch::new()?.track_cleanup(control);
    let mut writer = BufWriter::with_capacity(256 * 1024, &mut file.file);
    writer
        .write_all(b"{\"events\":[")
        .map_err(|e| e.to_string())?;
    let mut pending = Vec::new();
    let mut first = true;
    let mut coverage = None;
    let mut candidates = BTreeMap::new();
    while let Some(chunk) = wait_source(control, response.chunk(), SOURCE_IDLE_TIMEOUT)
        .await?
        .map_err(|e| e.to_string())?
    {
        control.advance(chunk.len() as u64)?;
        let mut offset = 0;
        for (index, byte) in chunk.iter().enumerate() {
            if *byte != b'\n' {
                continue;
            }
            pending.extend_from_slice(&chunk[offset..index]);
            offset = index + 1;
            control.check()?;
            let record: Value =
                serde_json::from_slice(&pending).map_err(|_| "invalid session stream record")?;
            pending.clear();
            match record.get("kind").and_then(Value::as_str) {
                Some("event") if coverage.is_none() => {
                    let event = record
                        .get("event")
                        .filter(|value| value.is_object())
                        .ok_or("missing session event")?;
                    let mut session = json!({"events": [event]});
                    super::collect_attachment_candidates(session_id, &session, &mut candidates);
                    let mut count = 0;
                    super::redact_provider_reasoning_states(&mut session, &mut count);
                    super::redact_json(&mut session, credentials, home, &mut count);
                    if !first {
                        writer.write_all(b",").map_err(|e| e.to_string())?;
                    }
                    first = false;
                    serde_json::to_writer(&mut writer, &session["events"][0])
                        .map_err(|e| e.to_string())?;
                }
                Some("coverage") if coverage.is_none() => {
                    if record.get("session_id").and_then(Value::as_str) != Some(session_id) {
                        return Err("session stream identity mismatch".into());
                    }
                    coverage = record
                        .get("coverage")
                        .filter(|value| value.is_object())
                        .cloned();
                    if coverage.is_none() {
                        return Err("missing session coverage".into());
                    }
                }
                Some("progress") if coverage.is_none() => {}
                _ => return Err("session stream failed or has invalid ordering".into()),
            }
        }
        pending.extend_from_slice(&chunk[offset..]);
    }
    if !pending.is_empty() {
        return Err("session stream ended within a record".into());
    }
    let mut coverage = coverage.ok_or("session stream ended without coverage")?;
    let complete = coverage
        .get("completeWithinRequestedRange")
        .and_then(Value::as_bool)
        .ok_or("invalid session coverage")?;
    let mut count = 0;
    super::redact_json(&mut coverage, credentials, home, &mut count);
    writer
        .write_all(b"],\"coverage\":")
        .map_err(|e| e.to_string())?;
    serde_json::to_writer(&mut writer, &coverage).map_err(|e| e.to_string())?;
    writer
        .write_all(b",\"session_id\":")
        .map_err(|e| e.to_string())?;
    serde_json::to_writer(
        &mut writer,
        &super::redact_text(session_id, credentials, home).0,
    )
    .map_err(|e| e.to_string())?;
    writer.write_all(b"}").map_err(|e| e.to_string())?;
    writer.flush().map_err(|e| e.to_string())?;
    drop(writer);
    file.rewind()?;
    for (key, mut item) in candidates {
        if let Some(existing) = attachments.get_mut(&key) {
            existing.references.append(&mut item.references);
        } else {
            attachments.insert(key, item);
        }
    }
    Ok(SessionExport { file, complete })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn unresponsive_source_times_out_without_a_total_export_deadline() {
        let control = Control::detached();
        let error = wait_source(
            &control,
            std::future::pending::<()>(),
            Duration::from_millis(5),
        )
        .await
        .unwrap_err();
        assert!(error.contains("stopped responding"));
        for _ in 0..3 {
            assert_eq!(
                wait_source(&control, std::future::ready(7), Duration::from_millis(5))
                    .await
                    .unwrap(),
                7
            );
        }
    }
}
