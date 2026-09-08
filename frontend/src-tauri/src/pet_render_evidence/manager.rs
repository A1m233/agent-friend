use super::{
    journal::{self, Journal},
    model::{Detector, Record, Sample, CONTEXT_LIMIT, EVENT_LIMIT},
};
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, SyncSender},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tokio::sync::oneshot;

enum Message {
    Event(Value),
    Mark(oneshot::Sender<Result<String, String>>),
}
#[derive(Default)]
struct Health {
    dropped: AtomicU64,
    invalid: AtomicU64,
    failed: AtomicU64,
    evicted: AtomicU64,
    stop: AtomicBool,
    done: AtomicBool,
}
#[derive(Clone)]
pub struct RenderEvidence {
    tx: SyncSender<Message>,
    health: Arc<Health>,
    gate: Arc<Mutex<(Instant, u32)>>,
}
struct Incident {
    id: String,
    signal: String,
    rows: Vec<Record>,
    bytes: usize,
    until: Instant,
    omitted: u64,
}
impl RenderEvidence {
    pub fn start(directory: PathBuf, session: String, identity: Value) -> Self {
        let (tx, rx) = mpsc::sync_channel(256);
        let health = Arc::new(Health::default());
        let h = health.clone();
        if std::thread::Builder::new()
            .name("pet-render-evidence".into())
            .spawn(move || {
                worker(rx, &h, directory, session, identity);
                h.done.store(true, Ordering::Relaxed);
            })
            .is_err()
        {
            health.failed.fetch_add(1, Ordering::Relaxed);
            health.done.store(true, Ordering::Relaxed);
        }
        Self {
            tx,
            health,
            gate: Arc::new(Mutex::new((Instant::now(), 0))),
        }
    }
    pub fn record(&self, event: Value) -> bool {
        if !super::model::valid_event(&event)
            || serde_json::to_vec(&event).map_or(true, |v| v.len() > EVENT_LIMIT)
        {
            self.health.invalid.fetch_add(1, Ordering::Relaxed);
            return false;
        }
        let admitted = self.gate.try_lock().is_ok_and(|mut g| {
            if g.0.elapsed() >= Duration::from_secs(1) {
                *g = (Instant::now(), 0);
            }
            if g.1 >= 32 {
                return false;
            }
            g.1 += 1;
            true
        });
        if !admitted {
            self.health.dropped.fetch_add(1, Ordering::Relaxed);
            return false;
        }
        if self.tx.try_send(Message::Event(event)).is_err() {
            self.health.dropped.fetch_add(1, Ordering::Relaxed);
            false
        } else {
            true
        }
    }
    pub fn sample(&self, s: Sample) -> bool {
        if !s.valid() {
            self.health.invalid.fetch_add(1, Ordering::Relaxed);
            return false;
        }
        self.record(json!({"kind":"render-sample","sample":s}))
    }
    pub fn snapshot(&self) -> Value {
        snapshot(&self.health, false)
    }
    pub fn shutdown(&self) {
        self.health.stop.store(true, Ordering::Release);
    }
    pub fn wait_for_shutdown(&self, timeout: Duration) -> bool {
        let until = Instant::now() + timeout;
        while !self.health.done.load(Ordering::Relaxed) && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(5));
        }
        self.health.done.load(Ordering::Relaxed)
    }
    pub async fn mark(&self) -> Result<String, String> {
        let (tx, rx) = oneshot::channel();
        self.tx
            .try_send(Message::Mark(tx))
            .map_err(|_| "record-queue-unavailable".to_string())?;
        tokio::time::timeout(Duration::from_secs(3), rx)
            .await
            .map_err(|_| "record-save-timeout: outcome unknown".to_string())?
            .map_err(|_| "record-writer-unavailable".to_string())?
    }
}
fn snapshot(h: &Health, end: bool) -> Value {
    json!({"schemaVersion":1,"supported":cfg!(target_os="windows"),
    "dropped":h.dropped.load(Ordering::Relaxed),"invalid":h.invalid.load(Ordering::Relaxed),
    "writeFailed":h.failed.load(Ordering::Relaxed),"evicted":h.evicted.load(Ordering::Relaxed),
    "writerStopped":h.done.load(Ordering::Relaxed),"normalEnd":end,"presentation":"unknown"})
}
fn worker(
    rx: mpsc::Receiver<Message>,
    h: &Health,
    directory: PathBuf,
    session: String,
    identity: Value,
) {
    let start = Instant::now();
    let now = || chrono::Utc::now().timestamp_millis();
    let mut journal = match Journal::open(directory.clone(), now()) {
        Ok(j) => j,
        Err(_) => {
            h.failed.fetch_add(1, Ordering::Relaxed);
            return;
        }
    };
    if let Ok(bytes) = journal::read_status(&directory) {
        if let Ok(v) = serde_json::from_slice::<Value>(&bytes) {
            for (key, counter) in [
                ("dropped", &h.dropped),
                ("invalid", &h.invalid),
                ("writeFailed", &h.failed),
                ("evicted", &h.evicted),
            ] {
                counter.fetch_add(v[key].as_u64().unwrap_or(0), Ordering::Relaxed);
            }
            journal.evicted += h.evicted.load(Ordering::Relaxed);
        }
    }
    let mut expects_samples = true;
    let mut display_on = true;
    let mut seq = 0u64;
    let mut detector = Detector::default();
    let mut context = VecDeque::<(usize, Record)>::new();
    let mut context_bytes = 0;
    let mut active: Option<Incident> = None;
    let mut last_signal = Instant::now()
        .checked_sub(Duration::from_secs(60))
        .unwrap_or(start);
    let mut last_sync = start;
    let mut last_status = start;
    let mut last_sample = start;

    let mut gap_reported = false;
    let mut initial = Some(json!({"kind":"process-start"}));
    loop {
        let ending = h.stop.load(Ordering::Acquire);
        let message = if let Some(e) = initial.take() {
            Some(Message::Event(e))
        } else if ending {
            rx.try_recv().ok()
        } else {
            rx.recv_timeout(Duration::from_millis(200)).ok()
        };
        let final_record = ending && message.is_none();
        let mut reply = None;
        let mut signal = None;
        let event = match message {
            Some(Message::Event(e)) => Some(e),
            Some(Message::Mark(tx)) => {
                reply = Some(tx);
                signal = Some("user-reported");
                Some(json!({"kind":"user-mark"}))
            }
            None if final_record => Some(json!({"kind":"process-end"})),
            None if expects_samples
                && display_on
                && !gap_reported
                && last_sample.elapsed() > Duration::from_secs(30) =>
            {
                gap_reported = true;
                signal = Some("observation-gap");
                Some(json!({"kind":"observation-gap","presentation":"unknown"}))
            }
            _ => None,
        };
        if let Some(event) = event {
            if event["kind"] == "display-state" {
                display_on = event["state"] == "on";
                last_sample = Instant::now();
            }
            if event["kind"] == "render-sample" {
                if let Ok(s) = serde_json::from_value::<Sample>(event["sample"].clone()) {
                    last_sample = Instant::now();
                    expects_samples = s.visible && s.kind != super::model::SampleKind::Stopped;
                    gap_reported = false;
                    signal = detector.observe(&s);
                }
            }
            if event["kind"] == "window-operation" && event["outcome"] == "error" {
                signal = Some("window-operation-failed");
            }
            seq += 1;
            let row = Record {
                schema_version: 1,
                process_session_id: session.clone(),
                identity: identity.clone(),
                record_seq: seq,
                recorded_at: now(),
                monotonic_ms: start.elapsed().as_millis() as u64,
                event,
            };
            let written = journal.append(&row);
            if written.is_err() {
                h.failed.fetch_add(1, Ordering::Relaxed);
            }
            let size = serde_json::to_vec(&row)
                .map(|v| v.len())
                .unwrap_or(EVENT_LIMIT);
            context_bytes += size;
            context.push_back((size, row.clone()));
            while context_bytes > CONTEXT_LIMIT
                || context
                    .front()
                    .is_some_and(|(_, r)| row.monotonic_ms.saturating_sub(r.monotonic_ms) > 60_000)
            {
                if let Some((n, _)) = context.pop_front() {
                    context_bytes -= n;
                }
            }
            if let Some(a) = &mut active {
                if a.bytes + size < CONTEXT_LIMIT + 128 * 1024 {
                    a.rows.push(row);
                    a.bytes += size;
                } else {
                    a.omitted += 1;
                }
            }
            if let Some(reason) = signal
                .filter(|_| reply.is_some() || last_signal.elapsed() > Duration::from_secs(60))
            {
                // A new user mark completes the old partial window without losing its evidence.
                if let Some(a) = active.take() {
                    if journal
                        .incident(&a.id, &a.signal, &a.rows, false, a.omitted)
                        .is_err()
                    {
                        h.failed.fetch_add(1, Ordering::Relaxed);
                    }
                }
                let persisted = if reply.is_some() {
                    journal::recent(&directory)
                } else {
                    Ok(context.iter().map(|(_, r)| r.clone()).collect())
                };
                let id = format!("{}-{}-{seq}", now(), std::process::id());
                let saved = persisted.and_then(|rows| {
                    journal.sync()?;
                    journal.incident(&id, reason, &rows, false, 0)?;
                    let bytes = serde_json::to_vec(&rows)?.len();
                    active = Some(Incident {
                        id: id.clone(),
                        signal: reason.into(),
                        rows,
                        bytes,
                        omitted: 0,
                        until: Instant::now() + Duration::from_secs(10),
                    });
                    Ok(())
                });
                if saved.is_err() {
                    h.failed.fetch_add(1, Ordering::Relaxed);
                }
                if let Some(tx) = reply.take() {
                    let _ = tx.send(saved.map(|()| id).map_err(|_| "record-save-failed".into()));
                }
                last_signal = Instant::now();
            }
        }
        if final_record || active.as_ref().is_some_and(|a| Instant::now() >= a.until) {
            if let Some(a) = active.take() {
                if journal
                    .incident(&a.id, &a.signal, &a.rows, !final_record, a.omitted)
                    .is_err()
                {
                    h.failed.fetch_add(1, Ordering::Relaxed);
                }
            }
        }
        if last_sync.elapsed() >= Duration::from_secs(5) || final_record {
            if journal.sync().is_err() {
                h.failed.fetch_add(1, Ordering::Relaxed);
            }
            last_sync = Instant::now();
        }
        if last_status.elapsed() >= Duration::from_secs(1) || final_record {
            h.evicted.store(journal.evicted, Ordering::Relaxed);
            if let Ok(b) = serde_json::to_vec(&snapshot(h, final_record)) {
                if journal::atomic_write(&directory.join("status.json"), &b).is_err() {
                    h.failed.fetch_add(1, Ordering::Relaxed);
                }
            }
            last_status = Instant::now();
        }
        if final_record {
            break;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn full_queue_is_nonblocking_and_counted() {
        let (tx, _rx) = mpsc::sync_channel(1);
        let m = RenderEvidence {
            tx,
            health: Arc::new(Health::default()),
            gate: Arc::new(Mutex::new((Instant::now(), 0))),
        };
        assert!(m.record(json!({"kind":"process-start"})));
        assert!(!m.record(json!({"kind":"process-start"})));
        assert_eq!(m.snapshot()["dropped"], 1);
        assert!(!m.record(json!({"kind":"process-start","untrusted":"content"})));
        assert_eq!(m.snapshot()["invalid"], 1);
    }
    #[tokio::test]
    async fn unavailable_disk_never_acknowledges_a_saved_mark() {
        let path =
            std::env::temp_dir().join(format!("render-write-failed-{}", rand::random::<u64>()));
        std::fs::write(&path, b"not-a-directory").unwrap();
        let m = RenderEvidence::start(
            path.clone(),
            "test-process".into(),
            json!({"buildMode":"debug"}),
        );
        assert!(m.mark().await.is_err());
        assert!(m.wait_for_shutdown(Duration::from_secs(2)));
        assert!(m.snapshot()["writeFailed"].as_u64().unwrap() > 0);
        std::fs::remove_file(path).unwrap();
    }
    #[tokio::test]
    async fn mark_ack_means_persisted_and_survives_new_manager() {
        let p = std::env::temp_dir().join(format!(
            "render-evidence-test-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let m = RenderEvidence::start(p.clone(), "first".into(), json!({"buildMode":"debug"}));
        m.record(json!({"kind":"window-operation","outcome":"error","window":"pet","action":"bounds","reason":"test","before":{},"after":{}}));
        let id = m.mark().await.unwrap();
        assert!(p.join(format!("incident-{id}.json")).exists());
        m.shutdown();
        for _ in 0..100 {
            if m.health.done.load(Ordering::Relaxed) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        let exported = journal::export(&p);
        assert!(exported.failures.is_empty());
        assert!(exported
            .entries
            .iter()
            .any(|(n, b)| n.contains("incident-") && String::from_utf8_lossy(b).contains("first")));
        assert_eq!(exported.coverage["normalEnd"], true);
        let next = RenderEvidence::start(p.clone(), "second".into(), json!({"buildMode":"debug"}));
        let next_id = next.mark().await.unwrap();
        let retained = std::fs::read_to_string(p.join(format!("incident-{next_id}.json"))).unwrap();
        assert!(retained.contains("first") && retained.contains("second"));
        next.shutdown();
        assert!(next.wait_for_shutdown(Duration::from_secs(2)));
        fs_cleanup(&p);
    }
    fn fs_cleanup(p: &std::path::Path) {
        std::fs::remove_dir_all(p).unwrap();
    }
}
