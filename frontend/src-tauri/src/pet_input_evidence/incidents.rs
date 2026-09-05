//! Signals retain evidence; they never repair, reject, or retry a business operation.
use super::{
    journal::{Journal, INCIDENT_FILE_BYTES},
    model::CONTEXT_BYTES,
};
use serde_json::{json, Value};
use std::collections::VecDeque;

struct Active {
    name: String,
    page: Value,
    signal: &'static str,
    started: u64,
    last_written: u64,
    last_context_seq: u64,
    first_at: Value,
    last_at: Value,
    count: u64,
    recovery: &'static str,
    closed: bool,
    last_request: Value,
    last_apply_seq: Option<u64>,
}
#[derive(Default)]
pub(super) struct Detector {
    context: VecDeque<(u64, usize, Value)>,
    context_bytes: usize,
    active: Vec<Active>,
    page: Value,
    seen_pages: VecDeque<Value>,
    last_front_seq: u64,
    display_revision: Option<u64>,
    mismatch_since: Option<f64>,
    mismatch_count: u64,
    match_since: Option<f64>,
    next_id: u64,
    last_sample: Option<u64>,
    last_sample_ms: Option<f64>,
}
impl Detector {
    pub fn observe(
        &mut self,
        journal: &mut Journal,
        event: &Value,
        mono: u64,
        now: i64,
    ) -> Result<(), &'static str> {
        let kind = event["kind"].as_str().unwrap_or("");
        let page = event.get("pageInstanceId").cloned().unwrap_or(Value::Null);
        if kind == "page-start" && page != self.page && !self.seen_pages.contains(&page) {
            for active in &mut self.active {
                // A native command can fail before this page's first diagnostic batch arrives.
                // Page startup must not prematurely close that same-page incident.
                if !active.page.is_null() && active.page != page {
                    active.closed = true;
                }
            }
            self.write_due(journal, mono, now, false)?;
            if !self.page.is_null() {
                self.seen_pages.push_back(self.page.clone());
                if self.seen_pages.len() > 32 {
                    self.seen_pages.pop_front();
                }
            }
            self.page = page.clone();
            self.last_front_seq = 0;
            self.reset_comparison();
        }
        let frontend = event["source"] == "frontend";
        let seq = event["frontendSeq"].as_u64().unwrap_or(0);
        let current =
            page.is_null() || (page == self.page && (!frontend || seq > self.last_front_seq));
        if frontend && current {
            self.last_front_seq = seq;
        }
        if current {
            if kind == "page-end" {
                for active in &mut self.active {
                    if active.page == page {
                        active.closed = true;
                    }
                }
            }
            if matches!(kind, "cursor-observed" | "hit-decision") {
                self.compare(journal, event, mono, now)?;
            }
        }
        // Native application results are authoritative regardless of diagnostic page-start
        // delivery. They are grouped by their declared page without changing the current page.
        // Late old-page facts can only update that old page's incident, never the new page's.
        if current || event["source"] == "native" {
            let outcome = event["outcome"].as_str().unwrap_or("");
            let failed = matches!(outcome, "error" | "not-applied" | "partial")
                || event["verified"] == false;
            let signal = match kind {
                "dpr-applied" | "dpr-ack" => Some("dpr-command-failed"),
                "passthrough-applied" if event["verified"] == false || outcome == "partial" => {
                    Some("passthrough-verification-failed")
                }
                "passthrough-applied" | "passthrough-ack" => Some("passthrough-command-failed"),
                _ => None,
            };
            if let Some(signal) = signal {
                if failed {
                    self.trigger(journal, event, signal, mono, now)?;
                } else if matches!(outcome, "applied" | "unchanged" | "ok") {
                    for active in &mut self.active {
                        if active.page == page
                            && is_corresponding_success(active, event)
                            && (active.signal == signal
                                || (kind == "passthrough-applied"
                                    && active.signal == "passthrough-verification-failed"))
                        {
                            active.closed = true;
                            active.recovery = "observed";
                        }
                    }
                }
            }
        }
        self.write_due(journal, mono, now, false)?;
        let size = serde_json::to_vec(event)
            .map_err(|_| "serialize-failed")?
            .len();
        self.context.push_back((mono, size, event.clone()));
        self.context_bytes += size;
        while self.context_bytes > CONTEXT_BYTES
            || self
                .context
                .front()
                .is_some_and(|(at, _, _)| mono.saturating_sub(*at) > 30_000)
        {
            if let Some((_, size, _)) = self.context.pop_front() {
                self.context_bytes = self.context_bytes.saturating_sub(size);
            }
        }
        Ok(())
    }
    fn reset_comparison(&mut self) {
        self.display_revision = None;
        self.mismatch_since = None;
        self.mismatch_count = 0;
        self.match_since = None;
        self.last_sample = None;
        self.last_sample_ms = None;
    }
    fn compare(
        &mut self,
        journal: &mut Journal,
        event: &Value,
        mono: u64,
        now: i64,
    ) -> Result<(), &'static str> {
        let (Some(actual), Some(scale), Some(display), Some(front_mono), Some(sample)) = (
            event["actualDpr"].as_f64(),
            event["scale"].as_f64(),
            event["displayRevision"].as_u64(),
            event["monotonicMs"].as_f64(),
            event["sampleId"].as_u64(),
        ) else {
            return Ok(());
        };
        if actual <= 0.0 || scale <= 0.0 {
            return Ok(());
        }
        if self.display_revision != Some(display) {
            self.reset_comparison();
            self.display_revision = Some(display);
        }
        if self.last_sample.is_some_and(|previous| sample <= previous)
            || self
                .last_sample_ms
                .is_some_and(|previous| front_mono <= previous)
        {
            return Ok(());
        }
        self.last_sample = Some(sample);
        self.last_sample_ms = Some(front_mono);
        if (actual - scale).abs() > 0.001 {
            self.match_since = None;
            let start = *self.mismatch_since.get_or_insert(front_mono);
            if front_mono < start {
                self.reset_comparison();
                return Ok(());
            }
            self.mismatch_count = self.mismatch_count.saturating_add(1);
            if self.mismatch_count >= 3 && front_mono - start >= 500.0 {
                self.trigger(journal, event, "dpr-mismatch", mono, now)?;
            }
        } else {
            self.mismatch_since = None;
            self.mismatch_count = 0;
            let start = *self.match_since.get_or_insert(front_mono);
            if front_mono - start >= 2000.0 {
                for active in &mut self.active {
                    if active.signal == "dpr-mismatch" && active.page == event["pageInstanceId"] {
                        active.closed = true;
                        active.recovery = "observed";
                    }
                }
            }
        }
        Ok(())
    }
    fn trigger(
        &mut self,
        journal: &mut Journal,
        event: &Value,
        signal: &'static str,
        mono: u64,
        now: i64,
    ) -> Result<(), &'static str> {
        let page = event.get("pageInstanceId").cloned().unwrap_or(Value::Null);
        if let Some(active) = self
            .active
            .iter_mut()
            .find(|a| !a.closed && a.signal == signal && a.page == page)
        {
            active.count = active.count.saturating_add(1);
            active.last_at = event["recordedAt"].clone();
            if active
                .last_request
                .as_u64()
                .zip(event["requestId"].as_u64())
                .map_or(true, |(previous, current)| current >= previous)
            {
                active.last_request = event["requestId"].clone();
            }
            active.last_apply_seq = event["applySeq"].as_u64().map(|current| {
                active
                    .last_apply_seq
                    .map_or(current, |previous| previous.max(current))
            });
            return Ok(());
        }
        // Keep active summaries bounded, including unannounced/late native page facts.
        if self.active.len() >= 8 {
            journal.bump("dropped", 1);
            return Ok(());
        }
        self.next_id = self.next_id.saturating_add(1);
        let id = format!("{}-{}", journal.process_session_id, self.next_id);
        let name = format!(
            "incident-{}-{}-{}.json",
            journal.process_session_id,
            self.next_id,
            now.max(0)
        );
        let context = self
            .context
            .iter()
            .map(|(_, _, event)| event.clone())
            .collect::<Vec<_>>();
        let mut value = json!({"schemaVersion":1,"identity":journal.identity,"processSessionId":journal.process_session_id,"incidentId":id,
            "pageInstanceId":page,"signal":signal,"firstRecordedAt":event["recordedAt"],"lastRecordedAt":event["recordedAt"],
            "count":1,"recovery":"unknown","closed":false,"truncated":false,"firstSignal":event,"context":context});
        trim_incident(&mut value, journal.limits.incident_file)?;
        journal.write_incident(&name, &value, now)?;
        self.active.push(Active {
            name,
            page,
            signal,
            started: mono,
            last_written: mono,
            last_context_seq: event["recordSeq"].as_u64().unwrap_or(0),
            first_at: event["recordedAt"].clone(),
            last_at: event["recordedAt"].clone(),
            count: 1,
            recovery: "unknown",
            closed: false,
            last_request: event["requestId"].clone(),
            last_apply_seq: event["applySeq"].as_u64(),
        });
        Ok(())
    }
    pub fn tick(&mut self, journal: &mut Journal, mono: u64, now: i64) -> Result<(), &'static str> {
        self.write_due(journal, mono, now, false)
    }
    pub fn end(&mut self, journal: &mut Journal, mono: u64, now: i64) -> Result<(), &'static str> {
        for active in &mut self.active {
            active.closed = true;
        }
        self.write_due(journal, mono, now, true)
    }
    fn write_due(
        &mut self,
        journal: &mut Journal,
        mono: u64,
        now: i64,
        force: bool,
    ) -> Result<(), &'static str> {
        for active in &mut self.active {
            if !force && !active.closed && mono.saturating_sub(active.last_written) < 1000 {
                continue;
            }
            let mut value = match journal.read_incident(&active.name) {
                Ok(v) => v,
                Err("incident-unavailable") => {
                    active.closed = true;
                    continue;
                }
                Err(e) => return Err(e),
            };
            value["firstRecordedAt"] = active.first_at.clone();
            value["lastRecordedAt"] = active.last_at.clone();
            value["count"] = json!(active.count);
            value["recovery"] = json!(active.recovery);
            value["closed"] = json!(active.closed);
            for (at, _, event) in &self.context {
                let seq = event["recordSeq"].as_u64().unwrap_or(0);
                if seq > active.last_context_seq && *at <= active.started.saturating_add(10_000) {
                    if let Some(context) = value["context"].as_array_mut() {
                        context.push(event.clone());
                    }
                    // Bound the intermediate JSON too, not only the final file.
                    trim_incident(&mut value, journal.limits.incident_file)?;
                    active.last_context_seq = seq;
                }
            }
            trim_incident(&mut value, journal.limits.incident_file)?;
            match journal.write_incident(&active.name, &value, now) {
                Err("incident-expired") => {
                    active.closed = true;
                    continue;
                }
                other => other?,
            }
            active.last_written = mono;
        }
        self.active.retain(|a| !a.closed);
        Ok(())
    }
}

fn is_corresponding_success(active: &Active, event: &Value) -> bool {
    if let (Some(previous), Some(current)) = (active.last_apply_seq, event["applySeq"].as_u64()) {
        return current > previous;
    }
    if let (Some(previous), Some(current)) =
        (active.last_request.as_u64(), event["requestId"].as_u64())
    {
        return current >= previous;
    }
    !active.last_request.is_null() && active.last_request == event["requestId"]
}

fn trim_incident(value: &mut Value, limit: usize) -> Result<(), &'static str> {
    // Keep the identity, first signal, and final summary even when the context is truncated.
    while serde_json::to_vec(value)
        .map_err(|_| "serialize-failed")?
        .len()
        > limit.min(INCIDENT_FILE_BYTES)
    {
        value["truncated"] = json!(true);
        let Some(context) = value["context"].as_array_mut() else {
            return Err("incident-too-large");
        };
        if context.is_empty() {
            return Err("incident-too-large");
        }
        context.remove(0);
    }
    Ok(())
}
