//! All caller work is bounded validation, try-lock and try-send; filesystem work is worker-only.
use super::{
    incidents::Detector,
    journal::{Journal, Limits},
    model,
};
use chrono::Utc;
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        mpsc::{self, Receiver, SyncSender, TrySendError},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};

// The approved queue ceiling is 1 MiB. Reserve half for incident JSON read/replace scratch.
pub(super) const ACTUAL_QUEUE_BYTES: usize = 512 * 1024;
#[derive(Default)]
struct Counters {
    dropped: AtomicU64,
    invalid: AtomicU64,
    rate_limited: AtomicU64,
    write_failed: AtomicU64,
    first_dropped: AtomicU64,
    last_dropped: AtomicU64,
    queued_bytes: AtomicUsize,
}
impl Counters {
    fn drop_record(&self, seq: u64) {
        self.dropped.fetch_add(1, Ordering::Relaxed);
        let _ = self
            .first_dropped
            .compare_exchange(0, seq, Ordering::Relaxed, Ordering::Relaxed);
        self.last_dropped.fetch_max(seq, Ordering::Relaxed);
    }
}
struct QueueItem {
    event: Value,
    bytes: usize,
    mono: u64,
    utc: i64,
}
struct Gate {
    control_tokens: f64,
    last_token_ms: u64,
    representative_second: u64,
    representatives: u8,
    batch_second: u64,
    batches: u8,
    coordinate_second: Option<u64>,
    last_hit: String,
}
impl Default for Gate {
    fn default() -> Self {
        Self {
            control_tokens: 64.0,
            last_token_ms: 0,
            representative_second: 0,
            representatives: 0,
            batch_second: 0,
            batches: 0,
            coordinate_second: None,
            last_hit: String::new(),
        }
    }
}
impl Gate {
    fn admit(&mut self, event: &mut Value, ms: u64) -> bool {
        let sec = ms / 1000;
        let kind = event["kind"].as_str().unwrap_or("");
        if matches!(kind, "hit-decision" | "cursor-observed") {
            if self.representative_second != sec {
                self.representative_second = sec;
                self.representatives = 0;
            }
            if self.representatives >= 4 {
                return false;
            }
            self.representatives += 1;
            if kind == "hit-decision" {
                let key = format!(
                    "{}:{}:{}:{}:{}",
                    event["pageInstanceId"],
                    event["reason"],
                    event["ignore"],
                    event["dragging"],
                    event["alphaHit"]
                );
                let transition = key != self.last_hit;
                self.last_hit = key;
                if !transition || self.coordinate_second == Some(sec) {
                    model::remove_coordinates(event);
                } else {
                    self.coordinate_second = Some(sec);
                }
            } else {
                model::remove_coordinates(event);
            }
            return true;
        }
        // Only selected hit decisions can carry ordinary cursor coordinates.
        // Viewport geometry is not a cursor trajectory and is preserved in display observations.
        if kind != "window-observed" {
            model::remove_coordinates(event);
        }
        let elapsed = ms.saturating_sub(self.last_token_ms);
        self.control_tokens = (self.control_tokens + elapsed as f64 * 32.0 / 1000.0).min(64.0);
        self.last_token_ms = ms;
        if self.control_tokens < 1.0 {
            return false;
        }
        self.control_tokens -= 1.0;
        true
    }
    fn batch(&mut self, ms: u64) -> bool {
        if self.batch_second != ms / 1000 {
            self.batch_second = ms / 1000;
            self.batches = 0;
        }
        if self.batches >= 4 {
            return false;
        }
        self.batches += 1;
        true
    }
}
struct Inner {
    sender: SyncSender<QueueItem>,
    session: String,
    started: Instant,
    next: AtomicU64,
    gate: Mutex<Gate>,
    counters: Arc<Counters>,
    stop: Arc<AtomicBool>,
    done: Arc<AtomicBool>,
}
impl Drop for Inner {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}
#[derive(Clone)]
pub(crate) struct PetInputEvidence {
    inner: Arc<Inner>,
}
impl PetInputEvidence {
    pub(crate) fn start(directory: PathBuf, process_session_id: String, identity: Value) -> Self {
        let (sender, receiver) = mpsc::sync_channel(model::QUEUE_ENTRIES);
        let counters = Arc::new(Counters::default());
        let stop = Arc::new(AtomicBool::new(false));
        let done = Arc::new(AtomicBool::new(false));
        let started = Instant::now();
        let inner = Arc::new(Inner {
            sender,
            session: process_session_id.clone(),
            started,
            next: AtomicU64::new(1),
            gate: Mutex::new(Gate::default()),
            counters: counters.clone(),
            stop: stop.clone(),
            done: done.clone(),
        });
        let spawn = std::thread::Builder::new()
            .name("pet-input-evidence".into())
            .spawn(move || {
                worker(
                    receiver,
                    directory,
                    process_session_id,
                    identity,
                    counters,
                    stop,
                    done,
                    started,
                )
            });
        if spawn.is_err() {
            inner.counters.write_failed.fetch_add(1, Ordering::Relaxed);
            inner.done.store(true, Ordering::Release);
        }
        Self { inner }
    }
    pub(crate) fn note_invalid(&self) {
        self.inner.counters.invalid.fetch_add(1, Ordering::Relaxed);
    }
    pub(crate) fn record_batch(&self, events: Value) -> bool {
        let Some(events) = events.as_array() else {
            self.note_invalid();
            return false;
        };
        let mut size = BoundedSize {
            bytes: 0,
            limit: 64 * 1024,
        };
        if events.len() > 16 || serde_json::to_writer(&mut size, events).is_err() {
            self.note_invalid();
            return false;
        }
        let ms = self.inner.started.elapsed().as_millis() as u64;
        let allowed = self
            .inner
            .gate
            .try_lock()
            .map(|mut gate| gate.batch(ms))
            .unwrap_or(false);
        if !allowed {
            self.inner
                .counters
                .rate_limited
                .fetch_add(events.len() as u64, Ordering::Relaxed);
            return false;
        }
        let mut complete = true;
        for event in events {
            if event["source"] != "frontend"
                || !model::validate_event(event)
                || matches!(
                    event["kind"].as_str(),
                    Some("dpr-applied" | "passthrough-applied" | "window-observed")
                )
            {
                self.note_invalid();
                complete = false;
                continue;
            }
            complete = self.record(event.clone()) && complete;
        }
        complete
    }
    pub(crate) fn record(&self, mut event: Value) -> bool {
        let seq = self.inner.next.fetch_add(1, Ordering::Relaxed);
        if self.inner.stop.load(Ordering::Acquire) {
            self.inner.counters.drop_record(seq);
            return false;
        }
        if !model::validate_event(&event) {
            self.note_invalid();
            self.inner.counters.drop_record(seq);
            return false;
        }
        let mono = self.inner.started.elapsed().as_millis() as u64;
        let admitted = self
            .inner
            .gate
            .try_lock()
            .map(|mut gate| gate.admit(&mut event, mono))
            .unwrap_or(false);
        if !admitted {
            self.inner
                .counters
                .rate_limited
                .fetch_add(1, Ordering::Relaxed);
            self.inner.counters.drop_record(seq);
            return false;
        }
        let utc = Utc::now();
        event["schemaVersion"] = json!(1);
        event["processSessionId"] = json!(self.inner.session);
        event["recordSeq"] = json!(seq);
        event["recordedAt"] = json!(utc.to_rfc3339_opts(chrono::SecondsFormat::Millis, true));
        event["processMonotonicMs"] = json!(mono);
        let Ok(encoded) = serde_json::to_vec(&event) else {
            self.note_invalid();
            return false;
        };
        let bytes = encoded.len() + 1;
        if bytes > model::EVENT_BYTES {
            self.note_invalid();
            self.inner.counters.drop_record(seq);
            return false;
        }
        if self
            .inner
            .counters
            .queued_bytes
            .fetch_update(Ordering::AcqRel, Ordering::Relaxed, |n| {
                n.checked_add(bytes).filter(|v| *v <= ACTUAL_QUEUE_BYTES)
            })
            .is_err()
        {
            self.inner.counters.drop_record(seq);
            return false;
        }
        let item = QueueItem {
            event,
            bytes,
            mono,
            utc: utc.timestamp_millis(),
        };
        match self.inner.sender.try_send(item) {
            Ok(()) => true,
            Err(TrySendError::Full(item) | TrySendError::Disconnected(item)) => {
                self.inner
                    .counters
                    .queued_bytes
                    .fetch_sub(item.bytes, Ordering::AcqRel);
                self.inner.counters.drop_record(seq);
                false
            }
        }
    }
    pub(crate) fn shutdown(&self) {
        self.inner.stop.store(true, Ordering::Release);
    }
    /// Exit-only grace period. Never joins the writer or waits past the bounded polling window.
    /// This is intentionally separate from the nonblocking hot-path/shutdown API.
    pub(crate) fn wait_for_shutdown(&self, timeout: Duration) -> bool {
        self.shutdown();
        let timeout = timeout.min(Duration::from_millis(200));
        let started = Instant::now();
        loop {
            if self.inner.done.load(Ordering::Acquire) {
                return true;
            }
            let Some(remaining) = timeout.checked_sub(started.elapsed()) else {
                return false;
            };
            if remaining.is_zero() {
                return false;
            }
            std::thread::park_timeout(remaining.min(Duration::from_millis(2)));
        }
    }
    /// Export-time supplemental status only; never substitutes for disk history.
    pub(crate) fn snapshot(&self) -> Value {
        json!({"scope":"export-time","processSessionId":self.inner.session,"queuedBytes":self.inner.counters.queued_bytes.load(Ordering::Relaxed),
            "dropped":self.inner.counters.dropped.load(Ordering::Relaxed),"invalid":self.inner.counters.invalid.load(Ordering::Relaxed),
            "writeFailed":self.inner.counters.write_failed.load(Ordering::Relaxed),"workerStopped":self.inner.done.load(Ordering::Acquire)})
    }
}

fn collector_event(session: &str, kind: &str, mono: u64, seq: u64) -> Value {
    json!({"schemaVersion":1,"source":"collector","kind":kind,"processSessionId":session,"recordSeq":seq,
        "recordedAt":Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis,true),"processMonotonicMs":mono})
}
fn worker(
    receiver: Receiver<QueueItem>,
    directory: PathBuf,
    session: String,
    identity: Value,
    counters: Arc<Counters>,
    stop: Arc<AtomicBool>,
    done: Arc<AtomicBool>,
    started: Instant,
) {
    let mut journal = None;
    let mut detector = Detector::default();
    let mut last_retry = 0;
    let mut last_flush = 0;
    let mut last_sync = 0;
    let mut last_heartbeat = 0;
    let mut last_page = Value::Null;
    let mut last_evidence = Value::Null;
    let mut collector_seq = 0u64;
    let mut previous_utc = Utc::now().timestamp_millis();
    loop {
        let mono = started.elapsed().as_millis() as u64;
        let now = Utc::now().timestamp_millis();
        if journal.is_none() && (last_retry == 0 || mono.saturating_sub(last_retry) >= 5000) {
            last_retry = mono.max(1);
            match Journal::open(
                directory.clone(),
                session.clone(),
                identity.clone(),
                Limits::default(),
            ) {
                Ok(mut opened) => {
                    let event = collector_event(&session, "process-start", mono, collector_seq);
                    if opened.append(&event, now).is_err() {
                        counters.write_failed.fetch_add(1, Ordering::Relaxed);
                    }
                    journal = Some(opened);
                }
                Err(_) => {
                    counters.write_failed.fetch_add(1, Ordering::Relaxed);
                }
            }
        }
        let item = receiver.recv_timeout(Duration::from_millis(100));
        let disconnected = matches!(item, Err(mpsc::RecvTimeoutError::Disconnected));
        if let Ok(mut item) = item {
            counters
                .queued_bytes
                .fetch_sub(item.bytes, Ordering::AcqRel);
            item.event["ingressSeq"] = item.event["recordSeq"].clone();
            collector_seq = collector_seq.saturating_add(1);
            item.event["recordSeq"] = json!(collector_seq);
            if item.event["source"] == "frontend" {
                last_page = json!({"pageInstanceId":item.event["pageInstanceId"],"frontendSeq":item.event["frontendSeq"],"recordedAt":item.event["recordedAt"]});
            }
            last_evidence =
                json!({"recordSeq":item.event["recordSeq"],"recordedAt":item.event["recordedAt"]});
            if let Some(journal) = journal.as_mut() {
                if item.event["kind"] == "coverage-gap" && item.event["source"] == "frontend" {
                    journal.bump(
                        "frontendDropped",
                        item.event["droppedCount"].as_u64().unwrap_or(0),
                    );
                }
                if item.event["kind"] == "hit-summary" {
                    journal.bump("aggregated", item.event["count"].as_u64().unwrap_or(0));
                }
                if item.utc < previous_utc {
                    journal.state["clockUncertain"] = json!(true);
                }
                previous_utc = item.utc;
                if journal
                    .append(&item.event, item.utc)
                    .and_then(|_| detector.observe(journal, &item.event, item.mono, item.utc))
                    .is_err()
                {
                    counters.write_failed.fetch_add(1, Ordering::Relaxed);
                }
            } else {
                counters.drop_record(item.event["ingressSeq"].as_u64().unwrap_or(0));
            }
        }
        let shutting_down =
            stop.load(Ordering::Acquire) && counters.queued_bytes.load(Ordering::Acquire) == 0;
        if let Some(journal) = journal.as_mut() {
            if mono.saturating_sub(last_flush) >= 1000 || shutting_down {
                for (key, counter) in [
                    ("dropped", &counters.dropped),
                    ("invalid", &counters.invalid),
                    ("rateLimited", &counters.rate_limited),
                    ("writeFailed", &counters.write_failed),
                ] {
                    let value = counter.swap(0, Ordering::AcqRel);
                    if value > 0 {
                        journal.bump(key, value);
                    }
                }
                let first = counters.first_dropped.swap(0, Ordering::AcqRel);
                let last = counters.last_dropped.swap(0, Ordering::AcqRel);
                if first > 0 {
                    journal.state["lastDroppedRange"] = json!({"processSessionId":session,"firstIngressSeq":first,"lastIngressSeq":last.max(first),"interior":"not-necessarily-contiguous"});
                }
                journal.state["lastPageReport"] = last_page.clone();
                journal.state["latestEvidence"] = last_evidence.clone();
                if mono.saturating_sub(last_heartbeat) >= 60_000 {
                    collector_seq = collector_seq.saturating_add(1);
                    let mut heartbeat =
                        collector_event(&session, "coverage-heartbeat", mono, collector_seq);
                    heartbeat["lastPageReport"] = last_page.clone();
                    heartbeat["latestEvidence"] = last_evidence.clone();
                    if journal.append(&heartbeat, now).is_err() {
                        counters.write_failed.fetch_add(1, Ordering::Relaxed);
                    }
                    last_heartbeat = mono;
                }
                if detector.tick(journal, mono, now).is_err() {
                    counters.write_failed.fetch_add(1, Ordering::Relaxed);
                }
                let sync = mono.saturating_sub(last_sync) >= 5000 || shutting_down;
                if journal
                    .flush(sync)
                    .and_then(|_| journal.persist_state(sync))
                    .is_err()
                {
                    counters.write_failed.fetch_add(1, Ordering::Relaxed);
                }
                last_flush = mono;
                if sync {
                    last_sync = mono;
                }
            }
            if shutting_down {
                collector_seq = collector_seq.saturating_add(1);
                let event = collector_event(&session, "process-end", mono, collector_seq);
                let _ = detector.end(journal, mono, now);
                let result = journal
                    .append(&event, now)
                    .and_then(|_| journal.flush(true));
                journal.state["normalEnd"] = json!(result.is_ok());
                let _ = journal.persist_state(true);
            }
        }
        if shutting_down || disconnected {
            break;
        }
    }
    done.store(true, Ordering::Release);
}

struct BoundedSize {
    bytes: usize,
    limit: usize,
}
impl std::io::Write for BoundedSize {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.limit.saturating_sub(self.bytes) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "evidence size limit",
            ));
        }
        self.bytes += bytes.len();
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn stalled_collector() -> (PetInputEvidence, Receiver<QueueItem>) {
        // A receiver deliberately never polled models a worker blocked in filesystem IO.
        // No filesystem or product runtime is started by this fixture.
        let (sender, receiver) = mpsc::sync_channel(model::QUEUE_ENTRIES);
        let inner = Inner {
            sender,
            session: "123-456".into(),
            started: Instant::now(),
            next: AtomicU64::new(1),
            gate: Mutex::new(Gate::default()),
            counters: Arc::new(Counters::default()),
            stop: Arc::new(AtomicBool::new(false)),
            done: Arc::new(AtomicBool::new(false)),
        };
        (
            PetInputEvidence {
                inner: Arc::new(inner),
            },
            receiver,
        )
    }

    #[test]
    fn sixty_hz_for_twenty_four_logical_hours_never_exceeds_four_per_second() {
        let mut gate = Gate::default();
        let mut event = json!({"kind":"cursor-observed"});
        let mut accepted = 0u64;
        for second in 0..24 * 60 * 60u64 {
            let mut this_second = 0;
            for frame in 0..60u64 {
                if gate.admit(&mut event, second * 1000 + frame * 1000 / 60) {
                    this_second += 1;
                }
            }
            assert_eq!(this_second, 4);
            accepted += this_second;
        }
        assert_eq!(accepted, 24 * 60 * 60 * 4);
        assert!(gate.last_hit.is_empty()); // No cursor samples/trajectory retained by the gate.
    }

    #[test]
    fn thousand_per_second_control_flood_is_bounded_at_every_logical_second() {
        let mut gate = Gate::default();
        let mut event = json!({"kind":"dpr-applied"});
        let mut accepted = 0;
        for ms in 0..10_000u64 {
            if gate.admit(&mut event, ms) {
                accepted += 1;
            }
            assert!(accepted <= 64 + (ms * 32 / 1000));
            assert!(gate.control_tokens >= 0.0 && gate.control_tokens <= 64.0);
        }
        assert!(accepted <= 384);
        assert!(accepted >= 380);
    }

    #[test]
    fn full_slow_sink_queue_drops_without_waiting_or_growing_bytes() {
        let (manager, receiver) = stalled_collector();
        let queued = json!({"source":"native","kind":"dpr-applied","outcome":"unchanged"});
        let bytes = serde_json::to_vec(&queued).unwrap().len();
        for _ in 0..model::QUEUE_ENTRIES {
            manager
                .inner
                .sender
                .try_send(QueueItem {
                    event: queued.clone(),
                    bytes,
                    mono: 0,
                    utc: 0,
                })
                .ok()
                .unwrap();
            manager
                .inner
                .counters
                .queued_bytes
                .fetch_add(bytes, Ordering::Relaxed);
        }
        let before = manager.inner.counters.queued_bytes.load(Ordering::Relaxed);
        let (finished, completion) = mpsc::channel();
        let caller = manager.clone();
        let thread = std::thread::spawn(move || {
            let rejected = (0..1000)
                .filter(|_| {
                    !caller.record(
                        json!({"source":"native","kind":"dpr-applied","outcome":"unchanged"}),
                    )
                })
                .count();
            finished.send(rejected).ok();
        });
        // This timeout detects a blocking send bug, not a real desktop latency benchmark.
        let result = completion.recv_timeout(Duration::from_secs(2));
        let after = manager.inner.counters.queued_bytes.load(Ordering::Relaxed);
        let still_queued = receiver.try_iter().count();
        drop(receiver);
        thread.join().unwrap();
        assert_eq!(result.unwrap(), 1000);
        assert_eq!(still_queued, model::QUEUE_ENTRIES);
        assert_eq!(after, before);
        assert!(after <= ACTUAL_QUEUE_BYTES);
        assert_eq!(manager.inner.counters.dropped.load(Ordering::Relaxed), 1000);
    }

    #[test]
    fn byte_budget_and_contended_gate_are_nonblocking_and_loss_is_visible() {
        let (manager, receiver) = stalled_collector();
        manager
            .inner
            .counters
            .queued_bytes
            .store(ACTUAL_QUEUE_BYTES, Ordering::Relaxed);
        assert!(!manager.record(json!({"source":"native","kind":"dpr-applied","outcome":"ok"})));
        assert_eq!(
            manager.inner.counters.queued_bytes.load(Ordering::Relaxed),
            ACTUAL_QUEUE_BYTES
        );
        assert!(receiver.try_recv().is_err());
        manager
            .inner
            .counters
            .queued_bytes
            .store(0, Ordering::Relaxed);
        let _held = manager.inner.gate.lock().unwrap();
        assert!(!manager.record(json!({"source":"native","kind":"dpr-applied","outcome":"ok"})));
        assert!(receiver.try_recv().is_err());
        assert_eq!(manager.inner.counters.dropped.load(Ordering::Relaxed), 2);
    }

    #[test]
    fn exit_only_wait_can_timeout_without_joining_a_stalled_writer() {
        let (manager, _receiver) = stalled_collector();
        assert!(!manager.wait_for_shutdown(Duration::ZERO));
        assert!(manager.inner.stop.load(Ordering::Acquire));
        assert!(!manager.wait_for_shutdown(Duration::from_millis(1)));
        assert!(!manager.record(json!({"source":"native","kind":"dpr-applied","outcome":"ok"})));
        manager.inner.done.store(true, Ordering::Release);
        assert!(manager.wait_for_shutdown(Duration::ZERO));
    }
    #[test]
    fn ordinary_coordinates_require_transitions_and_share_four_per_second() {
        let mut gate = Gate::default();
        let event = json!({"kind":"hit-decision","reason":"outside","ignore":true,"physical":{"x":1,"y":2}});
        let mut first = event.clone();
        assert!(gate.admit(&mut first, 0));
        assert!(first.get("physical").is_some());
        let mut stable = event.clone();
        assert!(gate.admit(&mut stable, 1000));
        assert!(stable.get("physical").is_none());
        for _ in 0..3 {
            assert!(gate.admit(&mut event.clone(), 1000));
        }
        assert!(!gate.admit(&mut event.clone(), 1000));
    }
    #[test]
    fn native_control_quota_is_independent_and_bounded() {
        let mut gate = Gate::default();
        for _ in 0..4 {
            assert!(gate.admit(&mut json!({"kind":"cursor-observed"}), 0));
        }
        for _ in 0..64 {
            assert!(gate.admit(&mut json!({"kind":"dpr-applied"}), 0));
        }
        assert!(!gate.admit(&mut json!({"kind":"dpr-applied"}), 0));
        for _ in 0..32 {
            assert!(gate.admit(&mut json!({"kind":"dpr-applied"}), 1000));
        }
        assert!(!gate.admit(&mut json!({"kind":"dpr-applied"}), 1000));
    }
    #[test]
    fn all_buffers_including_atomic_update_scratch_fit_budget() {
        assert!(
            256 * 1024
                + 64 * 1024
                + ACTUAL_QUEUE_BYTES
                + model::CONTEXT_BYTES
                + 3 * 256 * 1024
                + 128 * 1024
                + model::EVENT_BYTES
                <= 2 * 1024 * 1024
        );
    }
}
