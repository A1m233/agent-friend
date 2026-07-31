use super::{
    journal::ShellJournal,
    model::{
        now_rfc3339, RawShellEvent, ShellDiagnosticArtifact, ShellDiagnosticsExport,
        ShellJournalRecord, ShellSnapshot, SHELL_DIAGNOSTICS_SCHEMA_VERSION,
    },
    platform,
};
use std::{
    collections::BTreeMap,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, Receiver, SyncSender},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Manager, Runtime, Window, WindowEvent};
use tokio::sync::oneshot;

const EVENT_QUEUE_CAPACITY: usize = 512;
const SETTLE_DELAY: Duration = Duration::from_millis(50);
const MAX_BATCH_LATENCY: Duration = Duration::from_millis(500);
const IDLE_CONTROL_POLL: Duration = Duration::from_millis(100);
const MAX_EVENTS_PER_RECORD: usize = 64;
const EXPORT_TIMEOUT: Duration = Duration::from_secs(3);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(2);

enum ControlMessage {
    Export(oneshot::Sender<ShellDiagnosticsExport>),
    Shutdown(mpsc::SyncSender<()>),
}

pub(crate) struct ShellDiagnosticsManager {
    directory: std::path::PathBuf,
    event_tx: SyncSender<RawShellEvent>,
    event_rx: Mutex<Option<Receiver<RawShellEvent>>>,
    control_tx: mpsc::Sender<ControlMessage>,
    control_rx: Mutex<Option<Receiver<ControlMessage>>>,
    worker: Mutex<Option<JoinHandle<()>>>,
    observer: Mutex<Option<platform::ObserverHandle>>,
    dropped_events: Arc<AtomicU64>,
    started: AtomicBool,
}

impl ShellDiagnosticsManager {
    pub(crate) fn new(directory: std::path::PathBuf) -> Self {
        let (event_tx, event_rx) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
        let (control_tx, control_rx) = mpsc::channel();
        Self {
            directory,
            event_tx,
            event_rx: Mutex::new(Some(event_rx)),
            control_tx,
            control_rx: Mutex::new(Some(control_rx)),
            worker: Mutex::new(None),
            observer: Mutex::new(None),
            dropped_events: Arc::new(AtomicU64::new(0)),
            started: AtomicBool::new(false),
        }
    }

    pub(crate) fn start(self: &Arc<Self>, app: &AppHandle) -> Result<(), String> {
        if !platform::SUPPORTED || self.started.swap(true, Ordering::SeqCst) {
            return Ok(());
        }

        if let Err(error) = self.start_worker(app) {
            self.started.store(false, Ordering::SeqCst);
            return Err(error);
        }

        let observer = platform::start_observer(self.event_tx.clone(), self.dropped_events.clone());
        let observer_error =
            match observer {
                Ok(observer) => {
                    *self.observer.lock().map_err(|_| {
                        "shell diagnostics observer state is poisoned".to_string()
                    })? = Some(observer);
                    None
                }
                Err(error) => Some(error),
            };
        self.record_event(RawShellEvent::lifecycle("startup"));
        if observer_error.is_some() {
            self.record_event(RawShellEvent::lifecycle("observer-start-failed"));
        }
        observer_error.map_or(Ok(()), Err)
    }

    fn start_worker(&self, app: &AppHandle) -> Result<(), String> {
        let event_rx = self
            .event_rx
            .lock()
            .map_err(|_| "shell diagnostics event receiver is poisoned".to_string())?
            .take()
            .ok_or_else(|| "shell diagnostics event receiver is unavailable".to_string())?;
        let control_rx = self
            .control_rx
            .lock()
            .map_err(|_| "shell diagnostics control receiver is poisoned".to_string())?
            .take()
            .ok_or_else(|| "shell diagnostics control receiver is unavailable".to_string())?;
        let directory = self.directory.clone();
        let dropped_events = self.dropped_events.clone();
        let app_windows = collect_app_window_labels(app);
        let mut worker_slot = self
            .worker
            .lock()
            .map_err(|_| "shell diagnostics worker state is poisoned".to_string())?;
        let worker = thread::Builder::new()
            .name("shell-diagnostics-writer".into())
            .spawn(move || {
                worker_loop(directory, event_rx, control_rx, dropped_events, app_windows)
            })
            .map_err(|error| format!("start shell diagnostics writer failed: {error}"))?;
        *worker_slot = Some(worker);
        Ok(())
    }

    pub(crate) fn record_tauri_window_event<R: Runtime>(
        &self,
        window: &Window<R>,
        event: &WindowEvent,
    ) {
        if !platform::SUPPORTED || !self.started.load(Ordering::Relaxed) {
            return;
        }
        let kind = match event {
            WindowEvent::Resized(_) => "resized",
            WindowEvent::Moved(_) => "moved",
            WindowEvent::CloseRequested { .. } => "close-requested",
            WindowEvent::Destroyed => "destroyed",
            WindowEvent::Focused(true) => "focused",
            WindowEvent::Focused(false) => "blurred",
            WindowEvent::ScaleFactorChanged { .. } => "scale-factor-changed",
            _ => return,
        };
        let hwnd = window_hwnd(window);
        self.record_event(RawShellEvent::tauri(kind, hwnd, window.label()));
    }

    pub(crate) async fn export_for_bundle(&self) -> ShellDiagnosticsExport {
        if !platform::SUPPORTED {
            return unsupported_export();
        }
        if !self.started.load(Ordering::Relaxed) {
            return ShellDiagnosticsExport {
                supported: true,
                artifacts: Vec::new(),
                failures: vec!["shell diagnostics manager was not started".into()],
            };
        }

        let (response_tx, response_rx) = oneshot::channel();
        if let Err(error) = self.control_tx.send(ControlMessage::Export(response_tx)) {
            return ShellDiagnosticsExport {
                supported: true,
                artifacts: Vec::new(),
                failures: vec![format!("request shell diagnostics export failed: {error}")],
            };
        }
        match tokio::time::timeout(EXPORT_TIMEOUT, response_rx).await {
            Ok(Ok(export)) => export,
            Ok(Err(error)) => ShellDiagnosticsExport {
                supported: true,
                artifacts: Vec::new(),
                failures: vec![format!("shell diagnostics export was cancelled: {error}")],
            },
            Err(_) => ShellDiagnosticsExport {
                supported: true,
                artifacts: Vec::new(),
                failures: vec!["shell diagnostics export timed out".into()],
            },
        }
    }

    pub(crate) fn shutdown(&self) {
        if !platform::SUPPORTED || !self.started.swap(false, Ordering::SeqCst) {
            return;
        }
        if let Ok(mut observer) = self.observer.lock() {
            if let Some(observer) = observer.take() {
                observer.stop();
            }
        }
        let (done_tx, done_rx) = mpsc::sync_channel(1);
        if self
            .control_tx
            .send(ControlMessage::Shutdown(done_tx))
            .is_ok()
        {
            let _ = done_rx.recv_timeout(SHUTDOWN_TIMEOUT);
        }
        if let Ok(mut worker) = self.worker.lock() {
            if let Some(worker) = worker.take() {
                if worker.join().is_err() {
                    log::warn!("shell diagnostics writer thread panicked");
                }
            }
        }
    }

    fn record_event(&self, event: RawShellEvent) {
        if self.event_tx.try_send(event).is_err() {
            self.dropped_events.fetch_add(1, Ordering::Relaxed);
        }
    }
}

fn worker_loop(
    directory: std::path::PathBuf,
    event_rx: Receiver<RawShellEvent>,
    control_rx: Receiver<ControlMessage>,
    dropped_events: Arc<AtomicU64>,
    mut app_windows: BTreeMap<usize, String>,
) {
    let process_session_id = process_session_id();
    let mut journal_error = None;
    let mut journal = match ShellJournal::open(directory) {
        Ok(journal) => Some(journal),
        Err(error) => {
            log::warn!("shell diagnostics journal unavailable: {error}");
            journal_error = Some(error);
            None
        }
    };
    let mut pending = Vec::new();
    let mut batch_started = None;

    loop {
        while let Ok(control) = control_rx.try_recv() {
            match control {
                ControlMessage::Export(response_tx) => {
                    drain_queued_events(&event_rx, &mut pending, &mut app_windows);
                    let current = flush_pending(
                        &mut pending,
                        &mut batch_started,
                        &mut app_windows,
                        &mut journal,
                        &mut journal_error,
                        &dropped_events,
                        &process_session_id,
                    )
                    .unwrap_or_else(|| platform::capture_snapshot(&app_windows));
                    let _ = response_tx.send(build_export(
                        current,
                        journal.as_ref(),
                        journal_error.as_deref(),
                    ));
                }
                ControlMessage::Shutdown(done_tx) => {
                    drain_queued_events(&event_rx, &mut pending, &mut app_windows);
                    flush_pending(
                        &mut pending,
                        &mut batch_started,
                        &mut app_windows,
                        &mut journal,
                        &mut journal_error,
                        &dropped_events,
                        &process_session_id,
                    );
                    let _ = done_tx.send(());
                    return;
                }
            }
        }

        let wait = if pending.is_empty() {
            IDLE_CONTROL_POLL
        } else {
            SETTLE_DELAY
        };
        match event_rx.recv_timeout(wait) {
            Ok(event) => {
                update_app_window_labels(&mut app_windows, &event);
                if batch_started.is_none() {
                    batch_started = Some(Instant::now());
                }
                pending.push(event);
                if batch_started.is_some_and(|started| started.elapsed() >= MAX_BATCH_LATENCY) {
                    flush_pending(
                        &mut pending,
                        &mut batch_started,
                        &mut app_windows,
                        &mut journal,
                        &mut journal_error,
                        &dropped_events,
                        &process_session_id,
                    );
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                flush_pending(
                    &mut pending,
                    &mut batch_started,
                    &mut app_windows,
                    &mut journal,
                    &mut journal_error,
                    &dropped_events,
                    &process_session_id,
                );
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                flush_pending(
                    &mut pending,
                    &mut batch_started,
                    &mut app_windows,
                    &mut journal,
                    &mut journal_error,
                    &dropped_events,
                    &process_session_id,
                );
                return;
            }
        }
    }
}

fn drain_queued_events(
    event_rx: &Receiver<RawShellEvent>,
    pending: &mut Vec<RawShellEvent>,
    app_windows: &mut BTreeMap<usize, String>,
) {
    while let Ok(event) = event_rx.try_recv() {
        update_app_window_labels(app_windows, &event);
        pending.push(event);
    }
}

#[allow(clippy::too_many_arguments)]
fn flush_pending(
    pending: &mut Vec<RawShellEvent>,
    batch_started: &mut Option<Instant>,
    app_windows: &mut BTreeMap<usize, String>,
    journal: &mut Option<ShellJournal>,
    journal_error: &mut Option<String>,
    dropped_events: &AtomicU64,
    process_session_id: &str,
) -> Option<ShellSnapshot> {
    if pending.is_empty() {
        *batch_started = None;
        return None;
    }
    let snapshot = platform::capture_snapshot(app_windows);
    let total_events = pending.len();
    let retained_events = pending
        .drain(..total_events.min(MAX_EVENTS_PER_RECORD))
        .collect::<Vec<_>>();
    pending.clear();
    *batch_started = None;
    let dropped_event_count = dropped_events.swap(0, Ordering::Relaxed);

    if let Some(journal) = journal.as_mut() {
        let record = ShellJournalRecord {
            schema_version: SHELL_DIAGNOSTICS_SCHEMA_VERSION,
            sequence: journal.next_sequence(),
            process_session_id: process_session_id.to_string(),
            recorded_at: now_rfc3339(),
            events: retained_events,
            truncated_event_count: total_events.saturating_sub(MAX_EVENTS_PER_RECORD) as u64,
            dropped_event_count,
            snapshot: snapshot.clone(),
        };
        if let Err(error) = journal.append(&record) {
            if journal_error.as_deref() != Some(error.as_str()) {
                log::warn!("shell diagnostics journal write failed: {error}");
            }
            *journal_error = Some(error);
        }
    }
    Some(snapshot)
}

fn build_export(
    current: ShellSnapshot,
    journal: Option<&ShellJournal>,
    journal_error: Option<&str>,
) -> ShellDiagnosticsExport {
    let mut failures = journal_error
        .map(|error| vec![error.to_string()])
        .unwrap_or_default();
    let mut artifacts = Vec::new();
    match serde_json::to_vec_pretty(&current) {
        Ok(bytes) => artifacts.push(ShellDiagnosticArtifact {
            name: "diagnostics/windows-shell/current.json".into(),
            bytes,
        }),
        Err(error) => failures.push(format!(
            "serialize current shell diagnostics snapshot failed: {error}"
        )),
    }
    if let Some(journal) = journal {
        let (journal_artifacts, journal_failures) = journal.export_artifacts();
        artifacts.extend(journal_artifacts);
        failures.extend(journal_failures);
    }
    ShellDiagnosticsExport {
        supported: true,
        artifacts,
        failures,
    }
}

fn unsupported_export() -> ShellDiagnosticsExport {
    let snapshot = ShellSnapshot::unsupported();
    match serde_json::to_vec_pretty(&snapshot) {
        Ok(bytes) => ShellDiagnosticsExport {
            supported: false,
            artifacts: vec![ShellDiagnosticArtifact {
                name: "diagnostics/windows-shell/current.json".into(),
                bytes,
            }],
            failures: Vec::new(),
        },
        Err(error) => ShellDiagnosticsExport {
            supported: false,
            artifacts: Vec::new(),
            failures: vec![format!(
                "serialize unsupported shell diagnostics snapshot failed: {error}"
            )],
        },
    }
}

fn update_app_window_labels(app_windows: &mut BTreeMap<usize, String>, event: &RawShellEvent) {
    let (Some(label), Some(hwnd)) = (&event.app_window_label, &event.hwnd) else {
        return;
    };
    let Some(hwnd) = hwnd
        .strip_prefix("0x")
        .and_then(|value| usize::from_str_radix(value, 16).ok())
    else {
        return;
    };
    if event.kind == "destroyed" {
        app_windows.remove(&hwnd);
    } else {
        app_windows.insert(hwnd, label.clone());
    }
}

fn process_session_id() -> String {
    let started_at_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default();
    format!("{}-{started_at_ms}", std::process::id())
}

#[cfg(target_os = "windows")]
fn collect_app_window_labels(app: &AppHandle) -> BTreeMap<usize, String> {
    app.webview_windows()
        .into_iter()
        .filter_map(|(label, window)| window.hwnd().ok().map(|hwnd| (hwnd.0 as usize, label)))
        .collect()
}

#[cfg(not(target_os = "windows"))]
fn collect_app_window_labels(_app: &AppHandle) -> BTreeMap<usize, String> {
    BTreeMap::new()
}

#[cfg(target_os = "windows")]
fn window_hwnd<R: Runtime>(window: &Window<R>) -> Option<usize> {
    window.hwnd().ok().map(|hwnd| hwnd.0 as usize)
}

#[cfg(not(target_os = "windows"))]
fn window_hwnd<R: Runtime>(_window: &Window<R>) -> Option<usize> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_directory(name: &str) -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "agent-friend-shell-manager-{name}-{}-{nonce}",
            std::process::id()
        ))
    }

    #[test]
    fn app_window_mapping_is_updated_without_window_titles() {
        let mut labels = BTreeMap::new();
        let event = RawShellEvent::tauri("focused", Some(0x1234), "settings");
        update_app_window_labels(&mut labels, &event);
        assert_eq!(labels.get(&0x1234).map(String::as_str), Some("settings"));

        let destroyed = RawShellEvent::tauri("destroyed", Some(0x1234), "settings");
        update_app_window_labels(&mut labels, &destroyed);
        assert!(labels.is_empty());
    }

    #[test]
    fn export_barrier_drains_queued_events_and_updates_window_mapping() {
        let (event_tx, event_rx) = mpsc::sync_channel(2);
        event_tx
            .send(RawShellEvent::tauri("focused", Some(0x1234), "settings"))
            .expect("queue event");
        event_tx
            .send(RawShellEvent::tauri("destroyed", Some(0x1234), "settings"))
            .expect("queue event");

        let mut pending = Vec::new();
        let mut labels = BTreeMap::new();
        drain_queued_events(&event_rx, &mut pending, &mut labels);

        assert_eq!(pending.len(), 2);
        assert!(labels.is_empty());
    }

    #[test]
    fn queued_event_is_persisted_and_available_to_later_export() {
        let directory = test_directory("persist-export");
        let (event_tx, event_rx) = mpsc::sync_channel(4);
        let (control_tx, control_rx) = mpsc::channel();
        let worker_directory = directory.clone();
        let worker = thread::spawn(move || {
            worker_loop(
                worker_directory,
                event_rx,
                control_rx,
                Arc::new(AtomicU64::new(0)),
                BTreeMap::new(),
            )
        });

        event_tx
            .send(RawShellEvent::lifecycle("test-persisted-event"))
            .expect("queue event");
        let (export_tx, export_rx) = oneshot::channel();
        control_tx
            .send(ControlMessage::Export(export_tx))
            .expect("request export");
        let export = export_rx.blocking_recv().expect("receive export");
        let journal = export
            .artifacts
            .iter()
            .find(|artifact| artifact.name.ends_with(".jsonl"))
            .expect("journal artifact");
        assert!(String::from_utf8_lossy(&journal.bytes).contains("test-persisted-event"));

        let (done_tx, done_rx) = mpsc::sync_channel(1);
        control_tx
            .send(ControlMessage::Shutdown(done_tx))
            .expect("request shutdown");
        done_rx
            .recv_timeout(SHUTDOWN_TIMEOUT)
            .expect("worker shutdown");
        worker.join().expect("join worker");
        std::fs::remove_dir_all(directory).expect("cleanup test journal");
    }
}
