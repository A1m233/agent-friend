use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, State};

use crate::{paths, runtime_observability::RuntimeObserver};

#[cfg(target_os = "windows")]
mod windows;
#[cfg(target_os = "windows")]
mod windows_capture;

const EVENT_BUFFER_LIMIT: usize = 128;
const RENDER_EVIDENCE_SCHEMA_VERSION: u32 = 2;
const RENDER_EVIDENCE_LATEST_DIR: &str = "latest";
const RENDER_EVIDENCE_METADATA_FILE: &str = "capture.json";
const RENDER_EVIDENCE_WEBVIEW_FILE: &str = "webview2-pet.png";
const RENDER_EVIDENCE_DESKTOP_FILE: &str = "windows-desktop.png";
const RENDER_EVIDENCE_MAX_TOTAL_BYTES: u64 = 64 * 1024 * 1024;
const MIN_ARMED_HIDDEN_DURATION_MS: u64 = 500;
const FAILURE_REPORT_LIMIT: usize = 16;
const DISPLAY_PRESENTED_RECOVERY_PROBE_TIMEOUT_MS: u64 = 750;
const GPU_PRESENTED_RECOVERY_PROBE_TIMEOUT_MS: u64 = 2_500;
const GPU_PROCESS_FAILURE_DEDUP_WINDOW_MS: u64 = 1_000;

fn unix_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u64::MAX as u128) as u64)
        .unwrap_or_default()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RenderPixelMetrics {
    total_pixels: u64,
    transparent_pixel_count: u64,
    opaque_pixel_count: u64,
    near_black_opaque_pixel_count: u64,
    near_black_opaque_ratio: Option<f64>,
    #[serde(default)]
    near_white_opaque_pixel_count: u64,
    #[serde(default)]
    near_white_opaque_ratio: Option<f64>,
    average_opaque_luma: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RenderImageEvidence {
    file_name: String,
    capture_started_unix_ms: u64,
    capture_completed_unix_ms: u64,
    width: u32,
    height: u32,
    bytes: u64,
    sha256: String,
    pixel_metrics: RenderPixelMetrics,
}

impl RenderImageEvidence {
    fn transparent_ratio(&self) -> Option<f64> {
        (self.pixel_metrics.total_pixels > 0).then_some(
            self.pixel_metrics.transparent_pixel_count as f64
                / self.pixel_metrics.total_pixels as f64,
        )
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RecoveryRenderAssessment {
    generation: u64,
    baseline_capture_id: String,
    verdict: String,
    reasons: Vec<String>,
    baseline_desktop_near_black_ratio: Option<f64>,
    presented_desktop_near_black_ratio: Option<f64>,
    near_black_ratio_delta: Option<f64>,
    #[serde(default)]
    baseline_desktop_near_white_ratio: Option<f64>,
    #[serde(default)]
    presented_desktop_near_white_ratio: Option<f64>,
    #[serde(default)]
    near_white_ratio_delta: Option<f64>,
    baseline_webview_transparent_ratio: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RenderRecoveryTrigger {
    DisplayResume,
    WebView2GpuProcessExit,
}

impl RenderRecoveryTrigger {
    pub(crate) fn hidden_capture_trigger(self) -> &'static str {
        match self {
            Self::DisplayResume => "display-resume-hidden-baseline",
            Self::WebView2GpuProcessExit => "webview2-gpu-hidden-baseline",
        }
    }

    pub(crate) fn presented_capture_trigger(self) -> &'static str {
        match self {
            Self::DisplayResume => "display-resume-presented-probe",
            Self::WebView2GpuProcessExit => "webview2-gpu-presented-probe",
        }
    }

    pub(crate) fn observer_prefix(self) -> &'static str {
        match self {
            Self::DisplayResume => "display-recovery",
            Self::WebView2GpuProcessExit => "gpu-recovery",
        }
    }

    fn presented_probe_timeout_ms(self) -> u64 {
        match self {
            Self::DisplayResume => DISPLAY_PRESENTED_RECOVERY_PROBE_TIMEOUT_MS,
            Self::WebView2GpuProcessExit => GPU_PRESENTED_RECOVERY_PROBE_TIMEOUT_MS,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RenderEvidenceRecord {
    schema_version: u32,
    capture_id: String,
    trigger: String,
    hidden_duration_ms: Option<u64>,
    capture_started_at: String,
    capture_completed_at: String,
    start_delta_ms: Option<u64>,
    target_window: serde_json::Value,
    graphics: serde_json::Value,
    windows_shell_snapshot: serde_json::Value,
    webview2: Option<RenderImageEvidence>,
    desktop: Option<RenderImageEvidence>,
    errors: Vec<String>,
    #[serde(default)]
    automatic_incident: bool,
    #[serde(default)]
    recovery_generation: Option<u64>,
    #[serde(default)]
    recovery_assessment: Option<RecoveryRenderAssessment>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RenderEvidencePendingSummary {
    capture_id: String,
    captured_at: String,
    trigger: String,
    complete: bool,
    webview2_available: bool,
    desktop_available: bool,
    error_count: usize,
    automatic_incident: bool,
}

impl From<&RenderEvidenceRecord> for RenderEvidencePendingSummary {
    fn from(record: &RenderEvidenceRecord) -> Self {
        Self {
            capture_id: record.capture_id.clone(),
            captured_at: record.capture_completed_at.clone(),
            trigger: record.trigger.clone(),
            complete: record.webview2.is_some()
                && record.desktop.is_some()
                && record.errors.is_empty(),
            webview2_available: record.webview2.is_some(),
            desktop_available: record.desktop.is_some(),
            error_count: record.errors.len(),
            automatic_incident: record.automatic_incident,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RenderEvidenceStatus {
    supported: bool,
    armed: bool,
    waiting_for_visible_after_hidden: bool,
    capture_in_progress: bool,
    pending: Option<RenderEvidencePendingSummary>,
    storage_error: Option<String>,
}

pub(crate) struct RenderEvidenceArtifact {
    pub(crate) entry_name: String,
    pub(crate) bytes: Vec<u8>,
}

pub(crate) struct RenderEvidenceBundleExport {
    pub(crate) metadata: serde_json::Value,
    pub(crate) summary: serde_json::Value,
    pub(crate) artifacts: Vec<RenderEvidenceArtifact>,
    pub(crate) failures: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WebView2RuntimeInfo {
    browser_version: String,
    failure_report_folder_path: Option<String>,
    environment_registration_window: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WebView2DiagnosticEvent {
    sequence: u64,
    captured_at: String,
    event_type: &'static str,
    window_label: Option<String>,
    process_failed_kind: Option<String>,
    reason: Option<String>,
    exit_code: Option<i32>,
    process_description: Option<String>,
    browser_process_exit_kind: Option<String>,
    browser_process_id: Option<u32>,
    visibility_state: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WebView2RegistrationFailure {
    captured_at: String,
    window_label: Option<String>,
    stage: String,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WebView2FailureReport {
    file_name: String,
    bytes: u64,
    modified_at_unix_seconds: Option<u64>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WebViewDiagnosticsSnapshot {
    schema_version: u32,
    supported: bool,
    runtime: Option<WebView2RuntimeInfo>,
    failure_reports: Vec<WebView2FailureReport>,
    failure_report_scan_error: Option<String>,
    graphics: serde_json::Value,
    registered_windows: Vec<String>,
    registration_failures: Vec<WebView2RegistrationFailure>,
    event_buffer_limit: usize,
    dropped_event_count: u64,
    events: Vec<WebView2DiagnosticEvent>,
}

#[derive(Default)]
struct Inner {
    runtime: Option<WebView2RuntimeInfo>,
    attempted_windows: BTreeSet<String>,
    registered_windows: BTreeSet<String>,
    environment_registration_claimed: bool,
    registration_failures: Vec<WebView2RegistrationFailure>,
    next_sequence: u64,
    dropped_event_count: u64,
    events: VecDeque<WebView2DiagnosticEvent>,
    render_capture_armed: bool,
    render_hidden_since_unix_ms: Option<u64>,
    render_capture_in_progress: bool,
    last_gpu_process_failure_unix_ms: Option<u64>,
}

pub(crate) struct WebViewDiagnosticsManager {
    supported: bool,
    runtime_observer: Option<Arc<RuntimeObserver>>,
    render_evidence_dir: PathBuf,
    graphics: serde_json::Value,
    inner: Mutex<Inner>,
}

impl WebViewDiagnosticsManager {
    fn new(
        supported: bool,
        runtime_observer: Option<Arc<RuntimeObserver>>,
        render_evidence_dir: PathBuf,
        graphics: serde_json::Value,
    ) -> Self {
        Self {
            supported,
            runtime_observer,
            render_evidence_dir,
            graphics,
            inner: Mutex::new(Inner::default()),
        }
    }

    fn claim_window_registration(&self, label: &str) -> bool {
        self.inner
            .lock()
            .map(|mut inner| inner.attempted_windows.insert(label.to_string()))
            .unwrap_or(false)
    }

    fn mark_window_registered(&self, label: &str) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.registered_windows.insert(label.to_string());
        }
    }

    fn claim_environment_registration(&self) -> bool {
        self.inner
            .lock()
            .map(|mut inner| {
                if inner.environment_registration_claimed {
                    false
                } else {
                    inner.environment_registration_claimed = true;
                    true
                }
            })
            .unwrap_or(false)
    }

    fn set_runtime(&self, runtime: WebView2RuntimeInfo) {
        if let Some(observer) = &self.runtime_observer {
            observer.record(
                "webview2",
                "runtime-attached",
                json!({
                    "browserVersion": runtime.browser_version,
                    "failureReportFolderAvailable": runtime.failure_report_folder_path.is_some(),
                    "environmentRegistrationWindow": runtime.environment_registration_window,
                }),
            );
        }
        if let Ok(mut inner) = self.inner.lock() {
            inner.runtime = Some(runtime);
        }
    }

    fn record_registration_failure(
        &self,
        window_label: Option<&str>,
        stage: &str,
        message: impl Into<String>,
    ) {
        let message = message.into();
        log::warn!(
            "webview2 diagnostics registration failed: window={} stage={} error={}",
            window_label.unwrap_or("<environment>"),
            stage,
            message
        );
        if let Some(observer) = &self.runtime_observer {
            observer.record(
                "webview2",
                "registration-failed",
                json!({
                    "windowLabel": window_label,
                    "stage": stage,
                    "message": message,
                }),
            );
        }
        if let Ok(mut inner) = self.inner.lock() {
            inner
                .registration_failures
                .push(WebView2RegistrationFailure {
                    captured_at: Local::now().to_rfc3339(),
                    window_label: window_label.map(str::to_string),
                    stage: stage.to_string(),
                    message,
                });
        }
    }

    fn record_process_failed(
        &self,
        window_label: &str,
        process_failed_kind: String,
        reason: Option<String>,
        exit_code: Option<i32>,
        process_description: Option<String>,
    ) -> bool {
        self.record_process_failed_at(
            window_label,
            process_failed_kind,
            reason,
            exit_code,
            process_description,
            unix_time_ms(),
        )
    }

    fn record_process_failed_at(
        &self,
        window_label: &str,
        process_failed_kind: String,
        reason: Option<String>,
        exit_code: Option<i32>,
        process_description: Option<String>,
        recorded_at_unix_ms: u64,
    ) -> bool {
        log::error!(
            "webview2 process failed: window={} kind={} reason={} exitCode={}",
            window_label,
            process_failed_kind,
            reason.as_deref().unwrap_or("<unavailable>"),
            exit_code
                .map(|value| value.to_string())
                .unwrap_or_else(|| "<unavailable>".to_string())
        );
        if let Some(observer) = &self.runtime_observer {
            observer.record(
                "webview2",
                "process-failed",
                json!({
                    "windowLabel": window_label,
                    "processFailedKind": process_failed_kind,
                    "reason": reason,
                    "exitCode": exit_code,
                    "processDescription": process_description,
                }),
            );
        }
        let should_begin_gpu_recovery = if process_failed_kind == "gpu-process-exited" {
            self.inner
                .lock()
                .map(|mut inner| {
                    let duplicate =
                        inner
                            .last_gpu_process_failure_unix_ms
                            .is_some_and(|previous| {
                                recorded_at_unix_ms.saturating_sub(previous)
                                    <= GPU_PROCESS_FAILURE_DEDUP_WINDOW_MS
                            });
                    if !duplicate {
                        inner.last_gpu_process_failure_unix_ms = Some(recorded_at_unix_ms);
                    }
                    !duplicate
                })
                .unwrap_or(false)
        } else {
            false
        };
        self.push_event(WebView2DiagnosticEvent {
            sequence: 0,
            captured_at: Local::now().to_rfc3339(),
            event_type: "process-failed",
            window_label: Some(window_label.to_string()),
            process_failed_kind: Some(process_failed_kind),
            reason,
            exit_code,
            process_description,
            browser_process_exit_kind: None,
            browser_process_id: None,
            visibility_state: None,
        });
        should_begin_gpu_recovery
    }

    fn record_browser_process_exited(&self, exit_kind: String, process_id: u32) {
        if exit_kind == "failed" {
            log::error!(
                "webview2 browser process exited: kind={} processId={}",
                exit_kind,
                process_id
            );
        } else {
            log::info!(
                "webview2 browser process exited: kind={} processId={}",
                exit_kind,
                process_id
            );
        }
        if let Some(observer) = &self.runtime_observer {
            observer.record(
                "webview2",
                "browser-process-exited",
                json!({"browserProcessExitKind": exit_kind, "browserProcessId": process_id}),
            );
        }
        self.push_event(WebView2DiagnosticEvent {
            sequence: 0,
            captured_at: Local::now().to_rfc3339(),
            event_type: "browser-process-exited",
            window_label: None,
            process_failed_kind: None,
            reason: None,
            exit_code: None,
            process_description: None,
            browser_process_exit_kind: Some(exit_kind),
            browser_process_id: Some(process_id),
            visibility_state: None,
        });
    }

    pub(crate) fn record_recovery_event(
        &self,
        trigger: RenderRecoveryTrigger,
        event: &str,
        generation: u64,
        details: Value,
    ) {
        if let Some(observer) = &self.runtime_observer {
            observer.record(
                "webview2",
                &format!("{}-{event}", trigger.observer_prefix()),
                json!({
                    "generation": generation,
                    "details": details,
                }),
            );
        }
    }

    fn record_page_visibility(
        &self,
        window_label: &str,
        state: &str,
    ) -> Result<Option<u64>, String> {
        if !matches!(state, "hidden" | "visible") {
            return Err("visibility state must be hidden or visible".into());
        }
        let now_ms = unix_time_ms();
        if let Some(observer) = &self.runtime_observer {
            observer.record(
                "webview2",
                "page-visibility",
                json!({"windowLabel": window_label, "visibilityState": state}),
            );
        }
        self.push_event(WebView2DiagnosticEvent {
            sequence: 0,
            captured_at: Local::now().to_rfc3339(),
            event_type: "page-visibility",
            window_label: Some(window_label.to_string()),
            process_failed_kind: None,
            reason: None,
            exit_code: None,
            process_description: None,
            browser_process_exit_kind: None,
            browser_process_id: None,
            visibility_state: Some(state.to_string()),
        });

        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "WebView2 diagnostics state lock is poisoned".to_string())?;
        if state == "hidden" {
            if inner.render_capture_armed {
                inner.render_hidden_since_unix_ms.get_or_insert(now_ms);
            }
            return Ok(None);
        }
        if !inner.render_capture_armed || inner.render_capture_in_progress {
            return Ok(None);
        }
        let Some(hidden_since) = inner.render_hidden_since_unix_ms else {
            return Ok(None);
        };
        let hidden_duration_ms = now_ms.saturating_sub(hidden_since);
        if hidden_duration_ms < MIN_ARMED_HIDDEN_DURATION_MS {
            inner.render_hidden_since_unix_ms = None;
            return Ok(None);
        }
        inner.render_capture_armed = false;
        inner.render_hidden_since_unix_ms = None;
        inner.render_capture_in_progress = true;
        Ok(Some(hidden_duration_ms))
    }

    fn begin_manual_render_capture(&self) -> Result<(), String> {
        if !self.supported {
            return Err("render evidence capture is only supported on Windows".into());
        }
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| "WebView2 diagnostics state lock is poisoned".to_string())?;
        if inner.render_capture_in_progress {
            return Err("render evidence capture is already in progress".into());
        }
        self.cleanup_render_evidence_transients()?;
        inner.render_capture_armed = false;
        inner.render_hidden_since_unix_ms = None;
        inner.render_capture_in_progress = true;
        Ok(())
    }

    fn finish_render_capture(&self) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.render_capture_in_progress = false;
        }
    }

    pub(crate) fn set_render_capture_armed(
        &self,
        armed: bool,
    ) -> Result<RenderEvidenceStatus, String> {
        if !self.supported {
            return Err("render evidence capture is only supported on Windows".into());
        }
        {
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| "WebView2 diagnostics state lock is poisoned".to_string())?;
            if inner.render_capture_in_progress {
                return Err("render evidence capture is already in progress".into());
            }
            if armed {
                self.cleanup_render_evidence_transients()?;
            }
            inner.render_capture_armed = armed;
            inner.render_hidden_since_unix_ms = None;
        }
        if let Some(observer) = &self.runtime_observer {
            observer.record(
                "webview2",
                if armed {
                    "render-evidence-armed"
                } else {
                    "render-evidence-disarmed"
                },
                json!({"oneShot": true}),
            );
        }
        Ok(self.render_evidence_status())
    }

    pub(crate) fn render_evidence_status(&self) -> RenderEvidenceStatus {
        let (armed, waiting, in_progress) = self
            .inner
            .lock()
            .map(|inner| {
                (
                    inner.render_capture_armed,
                    inner.render_hidden_since_unix_ms.is_some(),
                    inner.render_capture_in_progress,
                )
            })
            .unwrap_or((false, false, false));
        match self.read_latest_render_evidence() {
            Ok(record) => RenderEvidenceStatus {
                supported: self.supported,
                armed,
                waiting_for_visible_after_hidden: waiting,
                capture_in_progress: in_progress,
                pending: record.as_ref().map(RenderEvidencePendingSummary::from),
                storage_error: None,
            },
            Err(error) => RenderEvidenceStatus {
                supported: self.supported,
                armed,
                waiting_for_visible_after_hidden: waiting,
                capture_in_progress: in_progress,
                pending: None,
                storage_error: Some(error),
            },
        }
    }

    fn latest_render_evidence_dir(&self) -> PathBuf {
        self.render_evidence_dir.join(RENDER_EVIDENCE_LATEST_DIR)
    }

    fn read_latest_render_evidence(&self) -> Result<Option<RenderEvidenceRecord>, String> {
        let latest = self.latest_render_evidence_dir();
        match fs::symlink_metadata(&latest) {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
            Ok(_) => return Err("render evidence latest path is not a regular directory".into()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(format!("inspect render evidence directory failed: {error}")),
        }
        let path = latest.join(RENDER_EVIDENCE_METADATA_FILE);
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(format!("read render evidence metadata failed: {error}")),
        };
        if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
            return Err("render evidence metadata is not a regular file".into());
        }
        if metadata.len() > 1024 * 1024 {
            return Err("render evidence metadata exceeds 1 MiB".into());
        }
        let bytes = fs::read(&path)
            .map_err(|error| format!("read render evidence metadata failed: {error}"))?;
        let record: RenderEvidenceRecord = serde_json::from_slice(&bytes)
            .map_err(|error| format!("parse render evidence metadata failed: {error}"))?;
        if !(1..=RENDER_EVIDENCE_SCHEMA_VERSION).contains(&record.schema_version) {
            return Err(format!(
                "unsupported render evidence schema version {}",
                record.schema_version
            ));
        }
        Ok(Some(record))
    }

    pub(crate) fn clear_render_evidence(&self) -> Result<RenderEvidenceStatus, String> {
        if self
            .inner
            .lock()
            .map(|inner| inner.render_capture_in_progress)
            .unwrap_or(true)
        {
            return Err("render evidence capture is in progress".into());
        }
        match fs::symlink_metadata(&self.render_evidence_dir) {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {
                fs::remove_dir_all(&self.render_evidence_dir)
                    .map_err(|error| format!("remove render evidence directory failed: {error}"))?;
            }
            Ok(_) => return Err("render evidence root is not a regular directory".into()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(format!("inspect render evidence path failed: {error}")),
        }
        Ok(self.render_evidence_status())
    }

    fn cleanup_render_evidence_transients(&self) -> Result<(), String> {
        match fs::symlink_metadata(&self.render_evidence_dir) {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
            Ok(_) => return Err("render evidence root is not a regular directory".into()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(format!("inspect render evidence root failed: {error}")),
        }
        let entries = match fs::read_dir(&self.render_evidence_dir) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(error) => {
                return Err(format!(
                    "read render evidence directory for cleanup failed: {error}"
                ))
            }
        };
        for entry in entries {
            let entry = entry.map_err(|error| {
                format!("read render evidence cleanup candidate failed: {error}")
            })?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if !name.starts_with("capture-")
                || !(name.ends_with(".staging") || name.ends_with(".previous"))
            {
                continue;
            }
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path).map_err(|error| {
                format!("inspect render evidence cleanup candidate failed: {error}")
            })?;
            if metadata.file_type().is_symlink() {
                return Err(format!(
                    "refusing to follow render evidence cleanup symlink: {name}"
                ));
            }
            if metadata.is_dir() {
                fs::remove_dir_all(&path).map_err(|error| {
                    format!("remove stale render evidence directory {name} failed: {error}")
                })?;
            } else if metadata.is_file() {
                fs::remove_file(&path).map_err(|error| {
                    format!("remove stale render evidence file {name} failed: {error}")
                })?;
            }
        }
        Ok(())
    }

    pub(crate) fn export_render_evidence_for_bundle(
        &self,
        include_requested: bool,
    ) -> RenderEvidenceBundleExport {
        let status = self.render_evidence_status();
        let mut artifacts = Vec::new();
        let mut failures = Vec::new();
        let record = match self.read_latest_render_evidence() {
            Ok(record) => record,
            Err(error) => {
                if include_requested {
                    failures.push(error);
                }
                None
            }
        };
        let automatic_incident = record
            .as_ref()
            .is_some_and(|record| record.automatic_incident);
        let include_capture = include_requested || automatic_incident;
        if include_capture && record.is_none() && failures.is_empty() {
            failures.push("no pending render evidence is available".into());
        }

        if include_capture {
            if let Some(record) = &record {
                failures.extend(record.errors.iter().cloned());
                let mut total_bytes = 0_u64;
                for (image, expected_file, entry_name) in [
                    (
                        record.webview2.as_ref(),
                        RENDER_EVIDENCE_WEBVIEW_FILE,
                        "render-evidence/webview2-pet.png",
                    ),
                    (
                        record.desktop.as_ref(),
                        RENDER_EVIDENCE_DESKTOP_FILE,
                        "render-evidence/windows-desktop.png",
                    ),
                ] {
                    let Some(image) = image else {
                        continue;
                    };
                    match self.validate_render_evidence_artifact(image, expected_file, total_bytes)
                    {
                        Ok(bytes) => {
                            total_bytes = total_bytes.saturating_add(image.bytes);
                            artifacts.push(RenderEvidenceArtifact {
                                entry_name: entry_name.into(),
                                bytes,
                            });
                        }
                        Err(error) => failures.push(error),
                    }
                }
            }
        }

        let artifact_names = artifacts
            .iter()
            .map(|artifact| artifact.entry_name.clone())
            .collect::<Vec<_>>();
        let metadata = json!({
            "schemaVersion": RENDER_EVIDENCE_SCHEMA_VERSION,
            "supported": self.supported,
            "includeRequested": include_requested,
            "automaticIncidentIncluded": automatic_incident,
            "status": status,
            "capture": if include_capture { record.as_ref() } else { None },
            "includedArtifacts": artifact_names,
        });
        let summary = json!({
            "supported": self.supported,
            "includeRequested": include_requested,
            "automaticIncidentIncluded": automatic_incident,
            "pendingCapture": record.as_ref().map(RenderEvidencePendingSummary::from),
            "artifacts": artifact_names,
            "failureCount": failures.len(),
            "maxTotalBytes": RENDER_EVIDENCE_MAX_TOTAL_BYTES,
        });
        RenderEvidenceBundleExport {
            metadata,
            summary,
            artifacts,
            failures,
        }
    }

    fn validate_render_evidence_artifact(
        &self,
        image: &RenderImageEvidence,
        expected_file: &str,
        current_total_bytes: u64,
    ) -> Result<Vec<u8>, String> {
        if image.file_name != expected_file {
            return Err(format!(
                "render evidence file name mismatch: expected {expected_file}, got {}",
                image.file_name
            ));
        }
        if current_total_bytes.saturating_add(image.bytes) > RENDER_EVIDENCE_MAX_TOTAL_BYTES {
            return Err(format!(
                "render evidence exceeds {} byte bundle limit",
                RENDER_EVIDENCE_MAX_TOTAL_BYTES
            ));
        }
        let path = self.latest_render_evidence_dir().join(expected_file);
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("inspect render evidence {expected_file} failed: {error}"))?;
        if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
            return Err(format!(
                "render evidence {expected_file} is not a regular file"
            ));
        }
        if metadata.len() != image.bytes {
            return Err(format!(
                "render evidence {expected_file} size mismatch: expected {}, got {}",
                image.bytes,
                metadata.len()
            ));
        }
        let bytes = fs::read(&path)
            .map_err(|error| format!("read render evidence {expected_file} failed: {error}"))?;
        if bytes.len() as u64 != image.bytes {
            return Err(format!(
                "render evidence {expected_file} changed while being read"
            ));
        }
        let actual_sha256 = format!("{:x}", Sha256::digest(&bytes));
        if actual_sha256 != image.sha256 {
            return Err(format!("render evidence {expected_file} SHA-256 mismatch"));
        }
        Ok(bytes)
    }

    async fn capture_render_evidence(
        &self,
        app: &AppHandle,
        trigger: &str,
        hidden_duration_ms: Option<u64>,
    ) -> Result<RenderEvidenceStatus, String> {
        #[cfg(target_os = "windows")]
        let result = windows_capture::capture_and_store(
            app,
            &self.render_evidence_dir,
            trigger,
            hidden_duration_ms,
            false,
            None,
        )
        .await;

        #[cfg(not(target_os = "windows"))]
        let result: Result<RenderEvidenceRecord, String> =
            Err("render evidence capture is only supported on Windows".into());

        self.finish_render_capture();
        match result {
            Ok(record) => {
                if let Some(observer) = &self.runtime_observer {
                    observer.record(
                        "webview2",
                        "render-evidence-captured",
                        json!({
                            "captureId": record.capture_id,
                            "trigger": record.trigger,
                            "webView2Available": record.webview2.is_some(),
                            "desktopAvailable": record.desktop.is_some(),
                            "errorCount": record.errors.len(),
                        }),
                    );
                }
                Ok(self.render_evidence_status())
            }
            Err(error) => {
                if let Some(observer) = &self.runtime_observer {
                    observer.record(
                        "webview2",
                        "render-evidence-capture-failed",
                        json!({"trigger": trigger, "message": error}),
                    );
                }
                Err(error)
            }
        }
    }

    pub(crate) async fn capture_recovery_baseline(
        &self,
        app: &AppHandle,
        generation: u64,
        hidden_duration_ms: u64,
        trigger: RenderRecoveryTrigger,
    ) -> Result<RenderEvidenceRecord, String> {
        self.begin_manual_render_capture()?;
        #[cfg(target_os = "windows")]
        let result = windows_capture::capture_and_store(
            app,
            &self.render_evidence_dir,
            trigger.hidden_capture_trigger(),
            Some(hidden_duration_ms),
            true,
            Some(generation),
        )
        .await;
        #[cfg(not(target_os = "windows"))]
        let result: Result<RenderEvidenceRecord, String> =
            Err("render recovery evidence is only supported on Windows".into());
        self.finish_render_capture();
        if let Some(observer) = &self.runtime_observer {
            match &result {
                Ok(record) => observer.record(
                    "webview2",
                    &format!("{}-baseline-captured", trigger.observer_prefix()),
                    json!({
                        "generation": generation,
                        "captureId": record.capture_id,
                        "webView2Available": record.webview2.is_some(),
                        "desktopAvailable": record.desktop.is_some(),
                        "errorCount": record.errors.len(),
                    }),
                ),
                Err(error) => observer.record(
                    "webview2",
                    &format!("{}-baseline-failed", trigger.observer_prefix()),
                    json!({"generation": generation, "message": error}),
                ),
            }
        }
        result
    }

    pub(crate) fn validate_recovery_baseline(
        &self,
        record: &RenderEvidenceRecord,
    ) -> Result<(), Vec<String>> {
        #[cfg(target_os = "windows")]
        return windows_capture::validate_hidden_recovery_baseline(record);
        #[cfg(not(target_os = "windows"))]
        Err(vec![
            "render recovery evidence is only supported on Windows".into(),
        ])
    }

    pub(crate) async fn capture_recovery_presented(
        &self,
        app: &AppHandle,
        baseline: &RenderEvidenceRecord,
        generation: u64,
        hidden_duration_ms: u64,
        trigger: RenderRecoveryTrigger,
    ) -> Result<bool, String> {
        self.begin_manual_render_capture()?;
        let timeout_ms = trigger.presented_probe_timeout_ms();
        #[cfg(target_os = "windows")]
        let result = tokio::time::timeout(
            std::time::Duration::from_millis(timeout_ms),
            windows_capture::capture_presented_recovery_and_store(
                app,
                &self.render_evidence_dir,
                baseline,
                generation,
                Some(hidden_duration_ms),
                trigger.presented_capture_trigger(),
            ),
        )
        .await
        .map_err(|_| format!("presented desktop recovery probe timed out after {timeout_ms} ms"))
        .and_then(|result| result);
        #[cfg(not(target_os = "windows"))]
        let result: Result<(RenderEvidenceRecord, bool), String> =
            Err("render recovery evidence is only supported on Windows".into());
        self.finish_render_capture();
        if let Some(observer) = &self.runtime_observer {
            match &result {
                Ok((record, safe)) => observer.record(
                    "webview2",
                    &format!("{}-presented-probed", trigger.observer_prefix()),
                    json!({
                        "generation": generation,
                        "captureId": record.capture_id,
                        "safe": safe,
                        "assessment": record.recovery_assessment.as_ref(),
                    }),
                ),
                Err(error) => observer.record(
                    "webview2",
                    &format!("{}-presented-probe-failed", trigger.observer_prefix()),
                    json!({"generation": generation, "message": error}),
                ),
            }
        }
        result.map(|(_, safe)| safe)
    }

    fn push_event(&self, mut event: WebView2DiagnosticEvent) {
        if let Ok(mut inner) = self.inner.lock() {
            inner.next_sequence = inner.next_sequence.saturating_add(1);
            event.sequence = inner.next_sequence;
            if inner.events.len() == EVENT_BUFFER_LIMIT {
                inner.events.pop_front();
                inner.dropped_event_count = inner.dropped_event_count.saturating_add(1);
            }
            inner.events.push_back(event);
        }
    }

    pub(crate) fn snapshot(&self) -> WebViewDiagnosticsSnapshot {
        let Ok(inner) = self.inner.lock() else {
            return WebViewDiagnosticsSnapshot {
                schema_version: 1,
                supported: self.supported,
                runtime: None,
                failure_reports: Vec::new(),
                failure_report_scan_error: Some(
                    "WebView2 diagnostics state lock is poisoned".to_string(),
                ),
                graphics: self.graphics.clone(),
                registered_windows: Vec::new(),
                registration_failures: vec![WebView2RegistrationFailure {
                    captured_at: Local::now().to_rfc3339(),
                    window_label: None,
                    stage: "snapshot".to_string(),
                    message: "WebView2 diagnostics state lock is poisoned".to_string(),
                }],
                event_buffer_limit: EVENT_BUFFER_LIMIT,
                dropped_event_count: 0,
                events: Vec::new(),
            };
        };
        let runtime = inner.runtime.clone();
        let registered_windows = inner.registered_windows.iter().cloned().collect();
        let registration_failures = inner.registration_failures.clone();
        let dropped_event_count = inner.dropped_event_count;
        let events = inner.events.iter().cloned().collect();
        drop(inner);
        let (failure_reports, failure_report_scan_error) = runtime
            .as_ref()
            .and_then(|runtime| runtime.failure_report_folder_path.as_deref())
            .map(scan_failure_reports)
            .unwrap_or_else(|| (Vec::new(), None));
        WebViewDiagnosticsSnapshot {
            schema_version: 1,
            supported: self.supported,
            runtime,
            failure_reports,
            failure_report_scan_error,
            graphics: self.graphics.clone(),
            registered_windows,
            registration_failures,
            event_buffer_limit: EVENT_BUFFER_LIMIT,
            dropped_event_count,
            events,
        }
    }
}

fn scan_failure_reports(path: &str) -> (Vec<WebView2FailureReport>, Option<String>) {
    let directory = Path::new(path);
    match fs::symlink_metadata(directory) {
        Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {}
        Ok(_) => {
            return (
                Vec::new(),
                Some("WebView2 failure report path is not a regular directory".into()),
            )
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return (Vec::new(), None),
        Err(error) => {
            return (
                Vec::new(),
                Some(format!(
                    "inspect WebView2 failure report directory failed: {error}"
                )),
            )
        }
    }
    let entries = match fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(error) => {
            return (
                Vec::new(),
                Some(format!(
                    "read WebView2 failure report directory failed: {error}"
                )),
            )
        }
    };
    let mut reports = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path
            .extension()
            .and_then(|extension| extension.to_str())
            .map_or(true, |extension| !extension.eq_ignore_ascii_case("dmp"))
        {
            continue;
        }
        let Ok(metadata) = fs::symlink_metadata(&path) else {
            continue;
        };
        if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
            continue;
        }
        reports.push(WebView2FailureReport {
            file_name: entry.file_name().to_string_lossy().into_owned(),
            bytes: metadata.len(),
            modified_at_unix_seconds: metadata
                .modified()
                .ok()
                .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
                .map(|duration| duration.as_secs()),
        });
    }
    reports.sort_by_key(|report| std::cmp::Reverse(report.modified_at_unix_seconds));
    reports.truncate(FAILURE_REPORT_LIMIT);
    (reports, None)
}

pub(crate) fn create_manager(
    runtime_observer: Arc<RuntimeObserver>,
) -> Arc<WebViewDiagnosticsManager> {
    #[cfg(target_os = "windows")]
    let graphics = windows_capture::capture_graphics_snapshot();
    #[cfg(not(target_os = "windows"))]
    let graphics = json!({"supported": false, "adapters": [], "errors": []});

    Arc::new(WebViewDiagnosticsManager::new(
        cfg!(target_os = "windows"),
        Some(runtime_observer),
        paths::webview_render_evidence_dir(),
        graphics,
    ))
}

pub(crate) fn install_existing_windows(app: &AppHandle, manager: &Arc<WebViewDiagnosticsManager>) {
    #[cfg(target_os = "windows")]
    windows::install_existing_windows(app, manager);

    #[cfg(not(target_os = "windows"))]
    {
        let _ = (app, manager);
    }
}

#[tauri::command]
pub(crate) fn get_webview_render_evidence_status(
    manager: State<'_, Arc<WebViewDiagnosticsManager>>,
) -> RenderEvidenceStatus {
    manager.render_evidence_status()
}

#[tauri::command]
pub(crate) fn set_webview_render_evidence_armed(
    armed: bool,
    manager: State<'_, Arc<WebViewDiagnosticsManager>>,
) -> Result<RenderEvidenceStatus, String> {
    manager.set_render_capture_armed(armed)
}

#[tauri::command]
pub(crate) fn clear_webview_render_evidence(
    manager: State<'_, Arc<WebViewDiagnosticsManager>>,
) -> Result<RenderEvidenceStatus, String> {
    manager.clear_render_evidence()
}

#[tauri::command]
pub(crate) async fn capture_webview_render_evidence_now(
    app: AppHandle,
    manager: State<'_, Arc<WebViewDiagnosticsManager>>,
) -> Result<RenderEvidenceStatus, String> {
    let manager = manager.inner().clone();
    manager.begin_manual_render_capture()?;
    manager
        .capture_render_evidence(&app, "manual-settings-action", None)
        .await
}

#[tauri::command]
pub(crate) async fn record_pet_webview_visibility(
    app: AppHandle,
    visibility_state: String,
    manager: State<'_, Arc<WebViewDiagnosticsManager>>,
) -> Result<RenderEvidenceStatus, String> {
    let manager = manager.inner().clone();
    let hidden_duration_ms = manager.record_page_visibility("pet", &visibility_state)?;
    if let Some(hidden_duration_ms) = hidden_duration_ms {
        manager
            .capture_render_evidence(
                &app,
                "pet-page-visible-after-hidden",
                Some(hidden_duration_ms),
            )
            .await?;
    }
    Ok(manager.render_evidence_status())
}

#[cfg(debug_assertions)]
#[tauri::command]
pub(crate) fn simulate_webview2_gpu_process_failure(
    app: AppHandle,
    manager: State<'_, Arc<WebViewDiagnosticsManager>>,
) -> bool {
    let should_begin_recovery = manager.record_process_failed(
        "debug-simulation",
        "gpu-process-exited".to_string(),
        Some("crashed".to_string()),
        Some(34),
        Some("debug-only simulated GPU process failure".to_string()),
    );
    if should_begin_recovery {
        crate::pet_display_recovery::handle_gpu_process_failure(&app, Some("crashed"), Some(34));
    }
    should_begin_recovery
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, time::SystemTime};

    fn test_manager(name: &str) -> WebViewDiagnosticsManager {
        WebViewDiagnosticsManager::new(
            true,
            None,
            std::env::temp_dir().join(format!("agent-friend-{name}")),
            json!({"supported": true}),
        )
    }

    fn test_render_record(capture_id: &str, bytes: &[u8]) -> RenderEvidenceRecord {
        let image = |file_name: &str| RenderImageEvidence {
            file_name: file_name.into(),
            capture_started_unix_ms: 100,
            capture_completed_unix_ms: 110,
            width: 1,
            height: 1,
            bytes: bytes.len() as u64,
            sha256: format!("{:x}", Sha256::digest(bytes)),
            pixel_metrics: RenderPixelMetrics {
                total_pixels: 1,
                transparent_pixel_count: 0,
                opaque_pixel_count: 1,
                near_black_opaque_pixel_count: 1,
                near_black_opaque_ratio: Some(1.0),
                near_white_opaque_pixel_count: 0,
                near_white_opaque_ratio: Some(0.0),
                average_opaque_luma: Some(0.0),
            },
        };
        RenderEvidenceRecord {
            schema_version: RENDER_EVIDENCE_SCHEMA_VERSION,
            capture_id: capture_id.into(),
            trigger: "test".into(),
            hidden_duration_ms: Some(1_000),
            capture_started_at: "2026-08-01T00:00:00Z".into(),
            capture_completed_at: "2026-08-01T00:00:01Z".into(),
            start_delta_ms: Some(10),
            target_window: json!({"label": "pet"}),
            graphics: json!({"supported": true}),
            windows_shell_snapshot: json!({"capability": "supported"}),
            webview2: Some(image(RENDER_EVIDENCE_WEBVIEW_FILE)),
            desktop: Some(image(RENDER_EVIDENCE_DESKTOP_FILE)),
            errors: Vec::new(),
            automatic_incident: false,
            recovery_generation: None,
            recovery_assessment: None,
        }
    }

    #[test]
    fn snapshot_is_bounded_and_reports_dropped_events() {
        let manager = test_manager("webview-event-buffer");
        for index in 0..EVENT_BUFFER_LIMIT + 2 {
            manager.record_process_failed(
                "pet",
                format!("kind-{index}"),
                Some("unexpected".to_string()),
                Some(index as i32),
                None,
            );
        }

        let snapshot = manager.snapshot();
        assert_eq!(snapshot.events.len(), EVENT_BUFFER_LIMIT);
        assert_eq!(snapshot.dropped_event_count, 2);
        assert_eq!(snapshot.events[0].sequence, 3);
        assert_eq!(snapshot.events.last().expect("last event").sequence, 130);
    }

    #[test]
    fn registration_claims_are_idempotent() {
        let manager = test_manager("webview-registration");
        assert!(manager.claim_window_registration("pet"));
        assert!(!manager.claim_window_registration("pet"));
        assert!(manager.claim_environment_registration());
        assert!(!manager.claim_environment_registration());
    }

    #[test]
    fn gpu_presented_probe_has_a_larger_budget_than_display_resume() {
        assert_eq!(
            RenderRecoveryTrigger::DisplayResume.presented_probe_timeout_ms(),
            750
        );
        assert_eq!(
            RenderRecoveryTrigger::WebView2GpuProcessExit.presented_probe_timeout_ms(),
            2_500
        );
    }

    #[test]
    fn shared_gpu_process_failure_starts_only_one_recovery_per_burst() {
        let manager = test_manager("webview-gpu-process-dedup");

        assert!(manager.record_process_failed_at(
            "pet",
            "gpu-process-exited".to_string(),
            Some("crashed".to_string()),
            Some(34),
            None,
            10_000,
        ));
        assert!(!manager.record_process_failed_at(
            "chat",
            "gpu-process-exited".to_string(),
            Some("crashed".to_string()),
            Some(34),
            None,
            10_500,
        ));
        assert!(manager.record_process_failed_at(
            "settings",
            "gpu-process-exited".to_string(),
            Some("crashed".to_string()),
            Some(34),
            None,
            11_001,
        ));
        assert_eq!(manager.snapshot().events.len(), 3);
    }

    #[test]
    fn non_gpu_process_failure_does_not_start_gpu_recovery() {
        let manager = test_manager("webview-render-process-no-gpu-recovery");

        assert!(!manager.record_process_failed_at(
            "chat",
            "render-process-exited".to_string(),
            Some("crashed".to_string()),
            Some(1),
            None,
            10_000,
        ));
    }

    #[test]
    fn armed_capture_requires_a_bounded_hidden_visible_transition() {
        let manager = test_manager("webview-render-trigger");
        manager.set_render_capture_armed(true).unwrap();

        assert_eq!(
            manager.record_page_visibility("pet", "visible").unwrap(),
            None
        );
        assert_eq!(
            manager.record_page_visibility("pet", "hidden").unwrap(),
            None
        );
        {
            let mut inner = manager.inner.lock().unwrap();
            inner.render_hidden_since_unix_ms = Some(unix_time_ms().saturating_sub(1_000));
        }
        let hidden_duration = manager
            .record_page_visibility("pet", "visible")
            .unwrap()
            .expect("capture trigger");
        assert!(hidden_duration >= 1_000);
        let status = manager.render_evidence_status();
        assert!(!status.armed);
        assert!(status.capture_in_progress);
    }

    #[test]
    fn render_evidence_bundle_export_is_opt_in_and_integrity_checked() {
        let nonce = unix_time_ms();
        let root = std::env::temp_dir().join(format!(
            "agent-friend-render-evidence-export-{}-{nonce}",
            std::process::id()
        ));
        let latest = root.join(RENDER_EVIDENCE_LATEST_DIR);
        fs::create_dir_all(&latest).unwrap();
        let bytes = b"png";
        let record = test_render_record("capture-1", bytes);
        fs::write(latest.join(RENDER_EVIDENCE_WEBVIEW_FILE), bytes).unwrap();
        fs::write(latest.join(RENDER_EVIDENCE_DESKTOP_FILE), bytes).unwrap();
        fs::write(
            latest.join(RENDER_EVIDENCE_METADATA_FILE),
            serde_json::to_vec(&record).unwrap(),
        )
        .unwrap();
        let manager =
            WebViewDiagnosticsManager::new(true, None, root.clone(), json!({"supported": true}));

        let excluded = manager.export_render_evidence_for_bundle(false);
        assert!(excluded.artifacts.is_empty());
        assert!(excluded.failures.is_empty());
        assert!(excluded.metadata["capture"].is_null());

        let included = manager.export_render_evidence_for_bundle(true);
        assert_eq!(included.artifacts.len(), 2);
        assert!(included.failures.is_empty());
        assert_eq!(included.metadata["capture"]["captureId"], "capture-1");

        let mut automatic_record = record.clone();
        automatic_record.automatic_incident = true;
        fs::write(
            latest.join(RENDER_EVIDENCE_METADATA_FILE),
            serde_json::to_vec(&automatic_record).unwrap(),
        )
        .unwrap();
        let automatic = manager.export_render_evidence_for_bundle(false);
        assert_eq!(automatic.artifacts.len(), 2);
        assert!(automatic.metadata["automaticIncidentIncluded"] == true);
        assert_eq!(automatic.metadata["capture"]["captureId"], "capture-1");

        fs::write(latest.join(RENDER_EVIDENCE_WEBVIEW_FILE), b"tampered").unwrap();
        let tampered = manager.export_render_evidence_for_bundle(true);
        assert_eq!(tampered.artifacts.len(), 1);
        assert!(tampered
            .failures
            .iter()
            .any(|failure| failure.contains("size mismatch")));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn failure_report_scan_is_bounded_and_does_not_read_dump_contents() {
        let nonce = unix_time_ms();
        let root = std::env::temp_dir().join(format!(
            "agent-friend-webview-failure-reports-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&root).unwrap();
        for index in 0..FAILURE_REPORT_LIMIT + 2 {
            fs::write(root.join(format!("report-{index:02}.dmp")), [index as u8]).unwrap();
        }
        fs::write(root.join("not-a-report.txt"), b"ignored").unwrap();

        let (reports, error) = scan_failure_reports(root.to_str().unwrap());

        assert!(error.is_none());
        assert_eq!(reports.len(), FAILURE_REPORT_LIMIT);
        assert!(reports
            .iter()
            .all(|report| report.file_name.ends_with(".dmp")));
        assert!(reports.iter().all(|report| report.bytes == 1));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn process_failures_are_persisted_for_post_restart_bundles() {
        let nonce = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "agent-friend-webview2-diagnostics-{}-{nonce}",
            std::process::id()
        ));
        let observer = RuntimeObserver::start(directory.clone());
        let manager = WebViewDiagnosticsManager::new(
            true,
            Some(observer),
            directory.join("render-evidence"),
            json!({"supported": true}),
        );

        manager.record_process_failed(
            "pet",
            "gpu-process-exited".to_string(),
            Some("crashed".to_string()),
            Some(-1),
            None,
        );

        let journal = fs::read_to_string(directory.join("tauri.log.runtime"))
            .expect("runtime journal should exist");
        assert!(journal.contains("\"source\":\"webview2\""));
        assert!(journal.contains("\"kind\":\"process-failed\""));
        assert!(journal.contains("\"processFailedKind\":\"gpu-process-exited\""));
        fs::remove_dir_all(directory).expect("cleanup");
    }
}
