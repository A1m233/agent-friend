//! One owned export, independent of the settings page lifetime.
use super::{GenerateDiagnosticBundleRequest, GenerateDiagnosticBundleResult};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, State};

const EVENT: &str = "diagnostic-export://changed";
static ORDINAL: AtomicU64 = AtomicU64::new(0);
pub(super) const CANCELLED: &str = "diagnostic export cancelled";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub id: String,
    pub ordinal: u64,
    pub version: u64,
    pub status: String,
    pub stage: String,
    pub processed_bytes: u64,
    pub elapsed_ms: u64,
    pub result: Option<GenerateDiagnosticBundleResult>,
    pub error: Option<String>,
    pub selection: Value,
    pub stages: Vec<Value>,
    #[serde(skip)]
    stage_started_ms: u64,
}

pub(super) struct Control {
    cancel: AtomicBool,
    state: Mutex<Snapshot>,
    started: Instant,
    last_emit: Mutex<Instant>,
    app: Option<AppHandle>,
    cleanup: super::scratch::CleanupWarnings,
}

impl Control {
    fn new(app: Option<AppHandle>) -> Arc<Self> {
        Arc::new(Self {
            cancel: AtomicBool::new(false),
            state: Mutex::new(Snapshot {
                id: format!("{:032x}", rand::random::<u128>()),
                ordinal: ORDINAL.fetch_add(1, Ordering::Relaxed) + 1,
                version: 1,
                status: "running".into(),
                stage: "preparing".into(),
                processed_bytes: 0,
                elapsed_ms: 0,
                result: None,
                error: None,
                selection: Value::Null,
                stages: Vec::new(),
                stage_started_ms: 0,
            }),
            started: Instant::now(),
            last_emit: Mutex::new(Instant::now()),
            app,
            cleanup: Arc::new(Mutex::new(Vec::new())),
        })
    }

    #[cfg(test)]
    pub fn detached() -> Arc<Self> {
        Self::new(None)
    }

    pub fn check(&self) -> Result<(), String> {
        if self.cancel.load(Ordering::Acquire) {
            Err(CANCELLED.into())
        } else {
            Ok(())
        }
    }

    pub fn cleanup_warnings(&self) -> super::scratch::CleanupWarnings {
        self.cleanup.clone()
    }

    fn snapshot(&self) -> Snapshot {
        let mut value = self.state.lock().unwrap().clone();
        if matches!(
            value.status.as_str(),
            "running" | "cancelling" | "publishing"
        ) {
            value.elapsed_ms = self.started.elapsed().as_millis() as u64;
        }
        value
    }

    pub fn metrics(&self) -> Value {
        let state = self.state.lock().unwrap();
        let now = self.started.elapsed().as_millis() as u64;
        let mut stages = state.stages.clone();
        if matches!(
            state.status.as_str(),
            "running" | "cancelling" | "publishing"
        ) {
            stages.push(json!({"stage":state.stage,"durationMs":now.saturating_sub(state.stage_started_ms),"processedBytes":state.processed_bytes}));
        }
        json!({"elapsedMs":now,"stages":stages})
    }

    fn emit(&self) {
        if let Some(app) = &self.app {
            let _ = app.emit(EVENT, self.snapshot());
        }
    }

    pub fn stage(&self, stage: &str) -> Result<(), String> {
        self.check()?;
        {
            let mut state = self.state.lock().unwrap();
            let now = self.started.elapsed().as_millis() as u64;
            let previous = json!({"stage":state.stage,"durationMs":now.saturating_sub(state.stage_started_ms),"processedBytes":state.processed_bytes});
            state.stages.push(previous);
            state.stage_started_ms = now;
            state.stage = stage.into();
            state.processed_bytes = 0;
            state.version += 1;
        }
        self.emit();
        Ok(())
    }

    pub fn advance(&self, bytes: u64) -> Result<(), String> {
        self.check()?;
        {
            let mut state = self.state.lock().unwrap();
            state.processed_bytes = state.processed_bytes.saturating_add(bytes);
            state.version += 1;
        }
        let mut last = self.last_emit.lock().unwrap();
        if last.elapsed() >= Duration::from_millis(250) {
            *last = Instant::now();
            self.emit();
        }
        Ok(())
    }

    // Serialize cancellation against publication. Once published, cancellation is too late.
    pub fn publish<T>(&self, action: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
        let mut state = self.state.lock().unwrap();
        self.check()?;
        let result = action()?;
        state.status = "publishing".into();
        state.version += 1;
        Ok(result)
    }

    fn request_cancel(&self) {
        {
            let mut state = self.state.lock().unwrap();
            if state.status != "running" && state.status != "cancelling" {
                return;
            }
            self.cancel.store(true, Ordering::Release);
            state.status = "cancelling".into();
            state.version += 1;
        }
        self.emit();
    }

    fn finish(&self, result: Result<GenerateDiagnosticBundleResult, String>) {
        let metrics = self.metrics();
        let cleanup = self.cleanup.lock().unwrap().clone();
        {
            let mut state = self.state.lock().unwrap();
            state.elapsed_ms = self.started.elapsed().as_millis() as u64;
            match result {
                Ok(mut result) => {
                    result.metrics = metrics;
                    for message in &cleanup {
                        result.warnings.push(super::CollectionFailure {
                            collection: "temporary-files".into(),
                            item: None,
                            message: message.clone(),
                        });
                    }
                    state.status = result.outcome.into();
                    state.result = Some(result);
                }
                Err(error) if self.cancel.load(Ordering::Acquire) => {
                    state.status = "cancelled".into();
                    if error != CANCELLED {
                        state.error = Some(error);
                    }
                }
                Err(error) => {
                    state.status = "failed".into();
                    state.error = Some(error);
                }
            }
            if state.result.is_none() && !cleanup.is_empty() {
                state.error = Some(format!(
                    "{}\n{}",
                    state.error.as_deref().unwrap_or(""),
                    cleanup.join("\n")
                ));
            }
            state.version += 1;
        }
        self.emit();
    }

    pub async fn wait<F: std::future::Future>(&self, future: F) -> Result<F::Output, String> {
        let mut future = std::pin::pin!(future);
        loop {
            self.check()?;
            match tokio::time::timeout(Duration::from_millis(100), &mut future).await {
                Ok(value) => return Ok(value),
                Err(_) => continue,
            }
        }
    }
}

#[derive(Default)]
pub struct Jobs(Mutex<Option<Arc<Control>>>);

#[tauri::command]
pub fn start_diagnostic_bundle(
    app: AppHandle,
    jobs: State<'_, Jobs>,
    request: GenerateDiagnosticBundleRequest,
) -> Result<Snapshot, String> {
    let mut active = jobs.0.lock().unwrap();
    if active.as_ref().is_some_and(|job| {
        matches!(
            job.snapshot().status.as_str(),
            "running" | "cancelling" | "publishing"
        )
    }) {
        return Err("已有诊断导出正在执行，请等待完成或取消。".into());
    }
    let control = Control::new(Some(app.clone()));
    control.state.lock().unwrap().selection = json!({
        "logs": if request.all_retained_logs {"all-retained"} else {"recent"},
        "sessionCount": request.session_ids.len(),
        "sessionTurnLimit": request.session_turn_limit,
        "includeAttachmentOriginals": request.include_attachment_originals,
        "includeRenderEvidence": request.include_render_evidence,
        "includeCrashDumps": request.include_crash_dumps,
        "verifyInstallFiles": request.verify_install_files,
    });
    let initial = control.snapshot();
    *active = Some(control.clone());
    tauri::async_runtime::spawn_blocking(move || {
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            tauri::async_runtime::block_on(super::run_bundle(app, request, control.clone()))
        }))
        .unwrap_or_else(|_| Err("诊断导出意外终止，请重试。".into()));
        control.finish(result);
    });
    Ok(initial)
}

#[tauri::command]
pub fn get_diagnostic_bundle_job(jobs: State<'_, Jobs>) -> Option<Snapshot> {
    jobs.0.lock().unwrap().as_ref().map(|job| job.snapshot())
}

#[tauri::command]
pub fn cancel_diagnostic_bundle(jobs: State<'_, Jobs>, job_id: String) -> Result<Snapshot, String> {
    let active = jobs.0.lock().unwrap();
    let job = active
        .as_ref()
        .filter(|job| job.snapshot().id == job_id)
        .ok_or("诊断任务已变化，请刷新状态。")?;
    job.request_cancel();
    Ok(job.snapshot())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cancellation_does_not_claim_completion_or_allow_publication() {
        let job = Control::detached();
        job.request_cancel();
        assert_eq!(job.snapshot().status, "cancelling");
        assert!(job.publish(|| Ok(())).is_err());
        job.finish(Err(CANCELLED.into()));
        assert_eq!(job.snapshot().status, "cancelled");
    }
    #[test]
    fn late_cancel_cannot_revoke_a_published_file() {
        let job = Control::detached();
        job.publish(|| Ok(())).unwrap();
        job.request_cancel();
        assert!(job.check().is_ok());
        assert_eq!(job.snapshot().status, "publishing");
    }
}
