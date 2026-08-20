use super::pet_display_topology::DisplayTopologySnapshot;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{fs, path::Path, time::Duration};
use tauri::Manager;

pub const RECOVERY_POLL_INTERVAL_MS: u64 = 250;
pub const RECOVERY_MIN_HIDDEN_MS: u64 = 6_500;
pub const RECOVERY_STABLE_MS: u64 = 1_500;
pub const RECOVERY_TIMEOUT_MS: u64 = 12_000;
pub const RECOVERY_PRESENTED_PROBE_MAX_ATTEMPTS: u32 = 3;
pub const RECOVERY_PRESENTED_PROBE_RETRY_DELAY_MS: u64 = 500;
pub const RECOVERY_SAFETY_GATE_ENV: &str = "AGENT_FRIEND_DISPLAY_RECOVERY_SAFETY_GATE";
pub const GPU_RECOVERY_MIN_HIDDEN_MS: u64 = 1_500;
pub const GPU_RECOVERY_STABLE_MS: u64 = 500;
pub const GPU_RECOVERY_TIMEOUT_MS: u64 = 7_000;
pub const GPU_RECOVERY_RESTART_ENV: &str = "AGENT_FRIEND_WEBVIEW2_GPU_RESTART";
pub const GPU_RECOVERY_RESTART_COOLDOWN_MS: u64 = 5 * 60 * 1_000;
const GPU_RECOVERY_RESTART_GUARD_FILE: &str = "webview2-gpu-recovery-restart.json";
const GPU_RECOVERY_RESTART_GUARD_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RecoveryCause {
    DisplayResume,
    WebView2GpuProcessExit,
}

impl RecoveryCause {
    fn label(self) -> &'static str {
        match self {
            Self::DisplayResume => "display-resume",
            Self::WebView2GpuProcessExit => "webview2-gpu-process-exit",
        }
    }

    fn render_trigger(self) -> crate::webview_diagnostics::RenderRecoveryTrigger {
        match self {
            Self::DisplayResume => crate::webview_diagnostics::RenderRecoveryTrigger::DisplayResume,
            Self::WebView2GpuProcessExit => {
                crate::webview_diagnostics::RenderRecoveryTrigger::WebView2GpuProcessExit
            }
        }
    }

    fn min_hidden_ms(self) -> u64 {
        match self {
            Self::DisplayResume => RECOVERY_MIN_HIDDEN_MS,
            Self::WebView2GpuProcessExit => GPU_RECOVERY_MIN_HIDDEN_MS,
        }
    }

    fn stable_ms(self) -> u64 {
        match self {
            Self::DisplayResume => RECOVERY_STABLE_MS,
            Self::WebView2GpuProcessExit => GPU_RECOVERY_STABLE_MS,
        }
    }

    fn timeout_ms(self) -> u64 {
        match self {
            Self::DisplayResume => RECOVERY_TIMEOUT_MS,
            Self::WebView2GpuProcessExit => GPU_RECOVERY_TIMEOUT_MS,
        }
    }

    fn probe_reason(self) -> &'static str {
        match self {
            Self::DisplayResume => "display-resume-probe",
            Self::WebView2GpuProcessExit => "webview2-gpu-recovery-probe",
        }
    }

    fn complete_reason(self) -> &'static str {
        match self {
            Self::DisplayResume => "display-resume-complete",
            Self::WebView2GpuProcessExit => "webview2-gpu-recovery-complete",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ActiveRecovery {
    generation: u64,
    cause: RecoveryCause,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SessionDisplayState {
    Off,
    On,
    Dimmed,
    Unknown(u32),
}

impl SessionDisplayState {
    pub fn from_raw(raw: u32) -> Self {
        match raw {
            0 => Self::Off,
            1 => Self::On,
            2 => Self::Dimmed,
            value => Self::Unknown(value),
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::On => "on",
            Self::Dimmed => "dimmed",
            Self::Unknown(_) => "unknown",
        }
    }

    fn is_not_rendering(self) -> bool {
        matches!(self, Self::Off | Self::Dimmed)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DisplayObservation {
    None,
    BeginRecovery { generation: u64 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VisibilityCommand {
    Show,
    Hide,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PresentedProbeAction {
    Complete,
    Cancel,
    Retry,
    SuppressUnsafe,
    SuppressError,
    RestoreWithoutGate,
}

fn is_retryable_presented_probe_error(error: &str) -> bool {
    error.starts_with("presented desktop recovery probe timed out after ")
}

fn decide_presented_probe_action(
    result: &Result<bool, String>,
    current: bool,
    gate_enabled: bool,
    attempt: u32,
) -> PresentedProbeAction {
    if !current {
        return PresentedProbeAction::Cancel;
    }
    match result {
        Ok(true) => PresentedProbeAction::Complete,
        Ok(false) if gate_enabled => PresentedProbeAction::SuppressUnsafe,
        Ok(false) => PresentedProbeAction::RestoreWithoutGate,
        Err(error)
            if gate_enabled
                && is_retryable_presented_probe_error(error)
                && attempt < RECOVERY_PRESENTED_PROBE_MAX_ATTEMPTS =>
        {
            PresentedProbeAction::Retry
        }
        Err(_) if gate_enabled => PresentedProbeAction::SuppressError,
        Err(_) => PresentedProbeAction::RestoreWithoutGate,
    }
}

#[derive(Debug)]
pub struct PetDisplayRecoveryState {
    last_display_state: Option<SessionDisplayState>,
    protection_enabled: bool,
    desired_visible: bool,
    next_generation: u64,
    active_recovery: Option<ActiveRecovery>,
    safety_suppressed: Option<RecoveryCause>,
}

impl Default for PetDisplayRecoveryState {
    fn default() -> Self {
        Self {
            last_display_state: None,
            protection_enabled: true,
            desired_visible: true,
            next_generation: 0,
            active_recovery: None,
            safety_suppressed: None,
        }
    }
}

impl PetDisplayRecoveryState {
    pub fn observe_display_state(&mut self, state: SessionDisplayState) -> DisplayObservation {
        let previous = self.last_display_state.replace(state);

        if !self.protection_enabled {
            if self
                .active_recovery
                .is_some_and(|active| active.cause == RecoveryCause::DisplayResume)
            {
                self.active_recovery = None;
            }
            if self.safety_suppressed == Some(RecoveryCause::DisplayResume) {
                self.safety_suppressed = None;
            }
            return DisplayObservation::None;
        }

        if state.is_not_rendering() {
            if self
                .active_recovery
                .is_some_and(|active| active.cause == RecoveryCause::DisplayResume)
            {
                self.active_recovery = None;
            }
            return DisplayObservation::None;
        }

        if state != SessionDisplayState::On
            || !previous.is_some_and(SessionDisplayState::is_not_rendering)
            || !self.desired_visible
            || self
                .active_recovery
                .is_some_and(|active| active.cause == RecoveryCause::WebView2GpuProcessExit)
        {
            return DisplayObservation::None;
        }

        self.next_generation = self.next_generation.saturating_add(1);
        self.active_recovery = Some(ActiveRecovery {
            generation: self.next_generation,
            cause: RecoveryCause::DisplayResume,
        });
        self.safety_suppressed = None;
        DisplayObservation::BeginRecovery {
            generation: self.next_generation,
        }
    }

    pub fn set_desired_visible(&mut self, visible: bool) -> VisibilityCommand {
        self.desired_visible = visible;
        self.active_recovery = None;
        self.safety_suppressed = None;
        if visible {
            VisibilityCommand::Show
        } else {
            VisibilityCommand::Hide
        }
    }

    pub fn toggle_requested(&mut self, physically_visible: bool) -> VisibilityCommand {
        if !physically_visible
            && self.desired_visible
            && (self.active_recovery.is_some() || self.safety_suppressed.is_some())
        {
            self.active_recovery = None;
            self.safety_suppressed = None;
            return VisibilityCommand::Show;
        }
        self.set_desired_visible(!self.desired_visible)
    }

    pub fn is_current(&self, generation: u64) -> bool {
        self.active_recovery
            .is_some_and(|active| active.generation == generation)
    }

    fn should_present(&self, generation: u64) -> bool {
        self.is_current(generation) && self.desired_visible
    }

    pub fn complete(&mut self, generation: u64) -> Option<VisibilityCommand> {
        if !self.is_current(generation) {
            return None;
        }
        self.active_recovery = None;
        Some(if self.desired_visible {
            VisibilityCommand::Show
        } else {
            VisibilityCommand::Hide
        })
    }

    pub fn timeout(&mut self, generation: u64) -> bool {
        if !self.is_current(generation) {
            return false;
        }
        let cause = self.active_recovery.take().map(|active| active.cause);
        self.safety_suppressed = self.desired_visible.then_some(cause).flatten();
        true
    }

    fn begin_gpu_process_recovery(&mut self) -> u64 {
        self.next_generation = self.next_generation.saturating_add(1);
        self.active_recovery = Some(ActiveRecovery {
            generation: self.next_generation,
            cause: RecoveryCause::WebView2GpuProcessExit,
        });
        self.safety_suppressed = None;
        self.next_generation
    }

    pub fn disable_protection(&mut self) -> Option<VisibilityCommand> {
        self.protection_enabled = false;
        let interrupted_active = self
            .active_recovery
            .is_some_and(|active| active.cause == RecoveryCause::DisplayResume);
        if interrupted_active {
            self.active_recovery = None;
        }
        let interrupted_suppressed = self.safety_suppressed == Some(RecoveryCause::DisplayResume);
        if interrupted_suppressed {
            self.safety_suppressed = None;
        }
        let interrupted = interrupted_active || interrupted_suppressed;
        interrupted.then_some(if self.desired_visible {
            VisibilityCommand::Show
        } else {
            VisibilityCommand::Hide
        })
    }
}

#[derive(Default)]
pub struct TopologyStabilityTracker {
    last_snapshot: Option<DisplayTopologySnapshot>,
    unchanged_since_ms: u64,
}

impl TopologyStabilityTracker {
    pub fn observe(
        &mut self,
        snapshot: DisplayTopologySnapshot,
        elapsed_ms: u64,
        min_hidden_ms: u64,
        stable_ms: u64,
    ) -> bool {
        if self.last_snapshot.as_ref() != Some(&snapshot) {
            self.last_snapshot = Some(snapshot);
            self.unchanged_since_ms = elapsed_ms;
        }
        elapsed_ms >= min_hidden_ms
            && elapsed_ms.saturating_sub(self.unchanged_since_ms) >= stable_ms
    }
}

fn is_current(app: &tauri::AppHandle, generation: u64) -> bool {
    app.try_state::<crate::PetOverlayState>()
        .and_then(|state| {
            state
                .inner
                .lock()
                .ok()
                .map(|guard| guard.display_recovery.is_current(generation))
        })
        .unwrap_or(false)
}

fn should_present(app: &tauri::AppHandle, generation: u64) -> bool {
    app.try_state::<crate::PetOverlayState>()
        .and_then(|state| {
            state
                .inner
                .lock()
                .ok()
                .map(|guard| guard.display_recovery.should_present(generation))
        })
        .unwrap_or(false)
}

fn apply_visibility_command(
    app: &tauri::AppHandle,
    command: VisibilityCommand,
    reason: &'static str,
) -> Result<(), String> {
    let Some(pet) = app.get_webview_window("pet") else {
        return Ok(());
    };
    match command {
        VisibilityCommand::Show => {
            crate::reconcile_pet_overlay(app, reason, true);
            pet.show().map_err(|error| error.to_string())
        }
        VisibilityCommand::Hide => pet.hide().map_err(|error| error.to_string()),
    }
}

fn suppress_recovery(
    app: &tauri::AppHandle,
    generation: u64,
    cause: RecoveryCause,
    stage: &'static str,
    detail: &str,
) -> bool {
    let suppressed = app
        .try_state::<crate::PetOverlayState>()
        .and_then(|state| {
            state
                .inner
                .lock()
                .ok()
                .map(|mut guard| guard.display_recovery.timeout(generation))
        })
        .unwrap_or(false);
    if let Some(pet) = app.get_webview_window("pet") {
        if let Err(error) = pet.hide() {
            log::error!(
                "pet display recovery: safety hide failed generation={} stage={} error={}",
                generation,
                stage,
                error
            );
        }
    }
    if suppressed {
        log::error!(
            "pet display recovery suppressed: generation={} stage={} detail={}; pet remains safely hidden",
            generation,
            stage,
            detail
        );
    }
    record_recovery_event(
        app,
        cause,
        "suppressed",
        generation,
        json!({"stage": stage, "detail": detail, "current": suppressed}),
    );
    suppressed
}

fn safety_gate_enabled_from_value(value: Option<&str>) -> bool {
    !value.is_some_and(|value| {
        matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "off" | "disabled"
        )
    })
}

fn safety_gate_enabled() -> bool {
    safety_gate_enabled_from_value(std::env::var(RECOVERY_SAFETY_GATE_ENV).ok().as_deref())
}

fn gpu_restart_enabled_from_value(value: Option<&str>) -> bool {
    !value.is_some_and(|value| {
        matches!(
            value.trim().to_ascii_lowercase().as_str(),
            "0" | "false" | "off" | "disabled"
        )
    })
}

fn gpu_restart_enabled() -> bool {
    gpu_restart_enabled_from_value(std::env::var(GPU_RECOVERY_RESTART_ENV).ok().as_deref())
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct GpuRestartGuard {
    schema_version: u32,
    requested_at_unix_ms: u64,
    process_id: u32,
}

fn unix_time_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or_default()
}

fn claim_gpu_restart_at(path: &Path, now_unix_ms: u64) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
                return Err("GPU recovery restart guard is not a regular file".into());
            }
            if metadata.len() > 4 * 1024 {
                return Err("GPU recovery restart guard exceeds 4 KiB".into());
            }
            let bytes = fs::read(path)
                .map_err(|error| format!("read GPU recovery restart guard failed: {error}"))?;
            let guard: GpuRestartGuard = serde_json::from_slice(&bytes)
                .map_err(|error| format!("parse GPU recovery restart guard failed: {error}"))?;
            if guard.schema_version != GPU_RECOVERY_RESTART_GUARD_SCHEMA_VERSION {
                return Err(format!(
                    "unsupported GPU recovery restart guard schema version {}",
                    guard.schema_version
                ));
            }
            if now_unix_ms.saturating_sub(guard.requested_at_unix_ms)
                < GPU_RECOVERY_RESTART_COOLDOWN_MS
            {
                return Ok(false);
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            return Err(format!(
                "inspect GPU recovery restart guard failed: {error}"
            ))
        }
    }

    let parent = path
        .parent()
        .ok_or_else(|| "GPU recovery restart guard path has no parent".to_string())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("create GPU recovery restart guard directory failed: {error}"))?;
    let guard = GpuRestartGuard {
        schema_version: GPU_RECOVERY_RESTART_GUARD_SCHEMA_VERSION,
        requested_at_unix_ms: now_unix_ms,
        process_id: std::process::id(),
    };
    let bytes = serde_json::to_vec_pretty(&guard)
        .map_err(|error| format!("serialize GPU recovery restart guard failed: {error}"))?;
    fs::write(path, bytes)
        .map_err(|error| format!("write GPU recovery restart guard failed: {error}"))?;
    Ok(true)
}

fn record_recovery_event(
    app: &tauri::AppHandle,
    cause: RecoveryCause,
    event: &str,
    generation: u64,
    details: serde_json::Value,
) {
    if let Some(manager) =
        app.try_state::<std::sync::Arc<crate::webview_diagnostics::WebViewDiagnosticsManager>>()
    {
        manager.record_recovery_event(cause.render_trigger(), event, generation, details);
    }
}

fn request_guarded_gpu_restart(
    app: &tauri::AppHandle,
    generation: u64,
    stage: &'static str,
    detail: &str,
) {
    if !gpu_restart_enabled() {
        log::error!(
            "webview2 GPU recovery restart is disabled by {}; pet remains safely hidden",
            GPU_RECOVERY_RESTART_ENV
        );
        record_recovery_event(
            app,
            RecoveryCause::WebView2GpuProcessExit,
            "restart-disabled",
            generation,
            json!({"stage": stage, "detail": detail}),
        );
        return;
    }

    let guard_path = crate::paths::log_dir().join(GPU_RECOVERY_RESTART_GUARD_FILE);
    match claim_gpu_restart_at(&guard_path, unix_time_ms()) {
        Ok(true) => {
            log::error!(
                "webview2 GPU recovery remained unhealthy: generation={} stage={} detail={}; requesting one guarded application restart",
                generation,
                stage,
                detail
            );
            record_recovery_event(
                app,
                RecoveryCause::WebView2GpuProcessExit,
                "restart-requested",
                generation,
                json!({
                    "stage": stage,
                    "detail": detail,
                    "cooldownMs": GPU_RECOVERY_RESTART_COOLDOWN_MS,
                }),
            );
            app.restart();
        }
        Ok(false) => {
            log::error!(
                "webview2 GPU recovery restart blocked by cooldown: generation={} cooldown_ms={}; pet remains safely hidden",
                generation,
                GPU_RECOVERY_RESTART_COOLDOWN_MS
            );
            record_recovery_event(
                app,
                RecoveryCause::WebView2GpuProcessExit,
                "restart-blocked",
                generation,
                json!({"reason": "cooldown", "cooldownMs": GPU_RECOVERY_RESTART_COOLDOWN_MS}),
            );
        }
        Err(error) => {
            log::error!(
                "webview2 GPU recovery restart guard failed: generation={} error={}; pet remains safely hidden",
                generation,
                error
            );
            record_recovery_event(
                app,
                RecoveryCause::WebView2GpuProcessExit,
                "restart-blocked",
                generation,
                json!({"reason": "guard-error", "message": error}),
            );
        }
    }
}

fn fail_recovery(
    app: &tauri::AppHandle,
    generation: u64,
    cause: RecoveryCause,
    stage: &'static str,
    detail: &str,
) {
    if suppress_recovery(app, generation, cause, stage, detail)
        && cause == RecoveryCause::WebView2GpuProcessExit
    {
        request_guarded_gpu_restart(app, generation, stage, detail);
    }
}

fn complete_and_restore(
    app: &tauri::AppHandle,
    generation: u64,
    reason: &'static str,
) -> Result<bool, String> {
    let command = app.try_state::<crate::PetOverlayState>().and_then(|state| {
        state
            .inner
            .lock()
            .ok()
            .and_then(|mut guard| guard.display_recovery.complete(generation))
    });
    let Some(command) = command else {
        return Ok(false);
    };
    apply_visibility_command(app, command, reason)?;
    Ok(true)
}

pub(crate) fn set_desired_visibility(
    app: &tauri::AppHandle,
    visible: bool,
    reason: &'static str,
) -> Result<(), String> {
    let command = app
        .try_state::<crate::PetOverlayState>()
        .and_then(|state| {
            state
                .inner
                .lock()
                .ok()
                .map(|mut guard| guard.display_recovery.set_desired_visible(visible))
        })
        .unwrap_or(if visible {
            VisibilityCommand::Show
        } else {
            VisibilityCommand::Hide
        });
    apply_visibility_command(app, command, reason)
}

pub(crate) fn toggle_visibility(app: &tauri::AppHandle) -> Result<(), String> {
    let physically_visible = app
        .get_webview_window("pet")
        .and_then(|window| window.is_visible().ok())
        .unwrap_or(false);
    let command = app
        .try_state::<crate::PetOverlayState>()
        .and_then(|state| {
            state
                .inner
                .lock()
                .ok()
                .map(|mut guard| guard.display_recovery.toggle_requested(physically_visible))
        })
        .unwrap_or(if physically_visible {
            VisibilityCommand::Hide
        } else {
            VisibilityCommand::Show
        });
    apply_visibility_command(app, command, "tray-toggle")
}

pub(crate) fn disable_protection(app: &tauri::AppHandle) -> Result<(), String> {
    let command = app.try_state::<crate::PetOverlayState>().and_then(|state| {
        state
            .inner
            .lock()
            .ok()
            .and_then(|mut guard| guard.display_recovery.disable_protection())
    });
    let Some(command) = command else {
        return Ok(());
    };
    apply_visibility_command(app, command, "display-recovery-protection-disabled")
}

fn schedule(app: &tauri::AppHandle, generation: u64, cause: RecoveryCause) {
    let Some(pet) = app.get_webview_window("pet") else {
        return;
    };
    if let Err(error) = pet.hide() {
        log::warn!(
            "pet render recovery: temporary hide failed cause={} generation={} error={}",
            cause.label(),
            generation,
            error
        );
        fail_recovery(app, generation, cause, "initial-hide", &error.to_string());
        return;
    }

    log::info!(
        "pet render recovery started: cause={} generation={} min_hidden_ms={} stable_ms={} timeout_ms={} safety_gate_enabled={}",
        cause.label(),
        generation,
        cause.min_hidden_ms(),
        cause.stable_ms(),
        cause.timeout_ms(),
        cause == RecoveryCause::WebView2GpuProcessExit || safety_gate_enabled()
    );
    record_recovery_event(
        app,
        cause,
        "started",
        generation,
        json!({
            "minHiddenMs": cause.min_hidden_ms(),
            "stableMs": cause.stable_ms(),
            "timeoutMs": cause.timeout_ms(),
        }),
    );
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let started = std::time::Instant::now();
        let mut stability = TopologyStabilityTracker::default();
        loop {
            tokio::time::sleep(Duration::from_millis(RECOVERY_POLL_INTERVAL_MS)).await;
            if !is_current(&handle, generation) {
                log::info!(
                    "pet render recovery cancelled: cause={} generation={}",
                    cause.label(),
                    generation
                );
                return;
            }

            let elapsed_ms = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
            let stable = handle
                .get_webview_window("pet")
                .and_then(|window| crate::read_pet_display_topology(&window))
                .is_some_and(|snapshot| {
                    stability.observe(
                        snapshot,
                        elapsed_ms,
                        cause.min_hidden_ms(),
                        cause.stable_ms(),
                    )
                });
            if stable {
                let gate_enabled =
                    cause == RecoveryCause::WebView2GpuProcessExit || safety_gate_enabled();
                let Some(manager) = handle
                    .try_state::<std::sync::Arc<crate::webview_diagnostics::WebViewDiagnosticsManager>>()
                    .map(|state| state.inner().clone())
                else {
                    if gate_enabled {
                        fail_recovery(
                            &handle,
                            generation,
                            cause,
                            "diagnostics-unavailable",
                            "WebView diagnostics manager is unavailable",
                        );
                    } else if let Err(error) = complete_and_restore(
                        &handle,
                        generation,
                        "display-resume-gate-disabled",
                    ) {
                        log::warn!("pet display recovery legacy restore failed: {error}");
                    }
                    return;
                };
                let baseline = match manager
                    .capture_recovery_baseline(
                        &handle,
                        generation,
                        elapsed_ms,
                        cause.render_trigger(),
                    )
                    .await
                {
                    Ok(record) => record,
                    Err(error) => {
                        if gate_enabled {
                            fail_recovery(&handle, generation, cause, "baseline-capture", &error);
                        } else if let Err(restore_error) = complete_and_restore(
                            &handle,
                            generation,
                            "display-resume-gate-disabled",
                        ) {
                            log::warn!(
                                "pet display recovery legacy restore failed after baseline error: {restore_error}"
                            );
                        }
                        return;
                    }
                };
                if !is_current(&handle, generation) {
                    log::info!(
                        "pet render recovery cancelled after baseline: cause={} generation={}",
                        cause.label(),
                        generation
                    );
                    return;
                }
                if let Err(reasons) = manager.validate_recovery_baseline(&baseline) {
                    if gate_enabled {
                        fail_recovery(
                            &handle,
                            generation,
                            cause,
                            "baseline-validation",
                            &reasons.join("; "),
                        );
                        return;
                    }
                    log::warn!(
                        "pet display recovery ignored unsafe baseline because safety gate is disabled: generation={} reasons={}",
                        generation,
                        reasons.join("; ")
                    );
                }
                if !should_present(&handle, generation) {
                    if complete_and_restore(&handle, generation, cause.complete_reason())
                        .unwrap_or(false)
                    {
                        log::info!(
                            "pet render recovery completed from a healthy hidden baseline without presenting a user-hidden pet: cause={} generation={} elapsed_ms={}",
                            cause.label(),
                            generation,
                            elapsed_ms
                        );
                        record_recovery_event(
                            &handle,
                            cause,
                            "completed-hidden",
                            generation,
                            json!({"elapsedMs": elapsed_ms}),
                        );
                    }
                    return;
                }
                let mut attempt = 1;
                loop {
                    if let Err(error) = apply_visibility_command(
                        &handle,
                        VisibilityCommand::Show,
                        cause.probe_reason(),
                    ) {
                        fail_recovery(&handle, generation, cause, "probe-show", &error);
                        return;
                    }
                    let probe_elapsed_ms =
                        started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
                    let presented_safe = manager
                        .capture_recovery_presented(
                            &handle,
                            &baseline,
                            generation,
                            probe_elapsed_ms,
                            cause.render_trigger(),
                        )
                        .await;
                    match decide_presented_probe_action(
                        &presented_safe,
                        is_current(&handle, generation),
                        gate_enabled,
                        attempt,
                    ) {
                        PresentedProbeAction::Complete => {
                            if complete_and_restore(&handle, generation, cause.complete_reason())
                                .unwrap_or(false)
                            {
                                log::info!(
                                    "pet render recovery completed with render evidence: cause={} generation={} elapsed_ms={} attempts={}",
                                    cause.label(),
                                    generation,
                                    probe_elapsed_ms,
                                    attempt
                                );
                            }
                        }
                        PresentedProbeAction::Cancel => {
                            log::info!(
                                "pet render recovery cancelled after presented probe: cause={} generation={}",
                                cause.label(),
                                generation
                            );
                        }
                        PresentedProbeAction::Retry => {
                            let error = presented_safe
                                .as_ref()
                                .expect_err("retry is only selected for probe errors");
                            if let Err(hide_error) = apply_visibility_command(
                                &handle,
                                VisibilityCommand::Hide,
                                cause.probe_reason(),
                            ) {
                                fail_recovery(
                                    &handle,
                                    generation,
                                    cause,
                                    "probe-retry-hide",
                                    &format!(
                                        "probe error: {error}; retry hide failed: {hide_error}"
                                    ),
                                );
                                return;
                            }
                            log::warn!(
                                "pet display recovery presented probe failed; retrying safely hidden: generation={} attempt={} max_attempts={} retry_delay_ms={} error={}",
                                generation,
                                attempt,
                                RECOVERY_PRESENTED_PROBE_MAX_ATTEMPTS,
                                RECOVERY_PRESENTED_PROBE_RETRY_DELAY_MS,
                                error
                            );
                            tokio::time::sleep(Duration::from_millis(
                                RECOVERY_PRESENTED_PROBE_RETRY_DELAY_MS,
                            ))
                            .await;
                            if !is_current(&handle, generation) {
                                log::info!(
                                    "pet render recovery cancelled during presented probe retry delay: cause={} generation={}",
                                    cause.label(),
                                    generation
                                );
                                return;
                            }
                            attempt += 1;
                            continue;
                        }
                        PresentedProbeAction::SuppressUnsafe => fail_recovery(
                            &handle,
                            generation,
                            cause,
                            "presented-validation",
                            "presented desktop gained a near-black or near-white full-screen layer",
                        ),
                        PresentedProbeAction::SuppressError => {
                            let error = presented_safe
                                .as_ref()
                                .expect_err("suppression is only selected for probe errors");
                            fail_recovery(&handle, generation, cause, "presented-capture", error);
                        }
                        PresentedProbeAction::RestoreWithoutGate => {
                            match &presented_safe {
                                Ok(false) => log::warn!(
                                    "pet display recovery ignored unsafe presented probe because safety gate is disabled: generation={}",
                                    generation
                                ),
                                Err(error) => log::warn!(
                                    "pet display recovery ignored presented probe error because safety gate is disabled: generation={} error={}",
                                    generation,
                                    error
                                ),
                                Ok(true) => {}
                            }
                            let _ = complete_and_restore(
                                &handle,
                                generation,
                                "display-resume-gate-disabled",
                            );
                        }
                    }
                    break;
                }
                return;
            }

            if elapsed_ms >= cause.timeout_ms() {
                fail_recovery(
                    &handle,
                    generation,
                    cause,
                    "timeout",
                    &format!("recovery timed out after {elapsed_ms} ms"),
                );
                return;
            }
        }
    });
}

pub(crate) fn handle_gpu_process_failure(
    app: &tauri::AppHandle,
    reason: Option<&str>,
    exit_code: Option<i32>,
) {
    let generation = app
        .try_state::<crate::PetOverlayState>()
        .and_then(|overlay_state| {
            overlay_state
                .inner
                .lock()
                .ok()
                .map(|mut guard| guard.display_recovery.begin_gpu_process_recovery())
        });
    let Some(generation) = generation else {
        if let Some(pet) = app.get_webview_window("pet") {
            if let Err(error) = pet.hide() {
                log::error!(
                    "webview2 GPU process failure fallback hide failed before recovery state was available: {error}"
                );
            }
        }
        log::error!(
            "webview2 GPU process failure could not initialize recovery state; pet was hidden fail-closed"
        );
        return;
    };
    record_recovery_event(
        app,
        RecoveryCause::WebView2GpuProcessExit,
        "triggered",
        generation,
        json!({"reason": reason, "exitCode": exit_code}),
    );
    schedule(app, generation, RecoveryCause::WebView2GpuProcessExit);
}

pub(crate) fn handle_session_display_state(app: &tauri::AppHandle, raw_state: u32) {
    let state = SessionDisplayState::from_raw(raw_state);
    let protection_enabled = app
        .try_state::<std::sync::Arc<std::sync::Mutex<crate::settings::Settings>>>()
        .and_then(|settings| {
            settings
                .lock()
                .ok()
                .map(|settings| settings.display_recovery_protection_enabled)
        })
        .unwrap_or(false);
    log::info!(
        "windows session display state observed: state={} raw={} protection_enabled={}",
        state.label(),
        raw_state,
        protection_enabled
    );
    let observation = app
        .try_state::<crate::PetOverlayState>()
        .and_then(|overlay_state| {
            overlay_state.inner.lock().ok().map(|mut guard| {
                guard.display_recovery.protection_enabled = protection_enabled;
                guard.display_recovery.observe_display_state(state)
            })
        })
        .unwrap_or(DisplayObservation::None);
    if let DisplayObservation::BeginRecovery { generation } = observation {
        schedule(app, generation, RecoveryCause::DisplayResume);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pet_display_topology::{DisplayTopologySnapshot, MonitorSnapshot, OverlayBounds};

    fn topology(scale_milli: i32) -> DisplayTopologySnapshot {
        DisplayTopologySnapshot {
            monitors: vec![MonitorSnapshot {
                x: 0,
                y: 0,
                width: 1920,
                height: 1080,
                scale_milli,
            }],
            bounds: OverlayBounds {
                x: 0,
                y: 0,
                width: 1920,
                height: 1080,
            },
        }
    }

    #[test]
    fn initial_and_duplicate_on_notifications_do_not_start_recovery() {
        let mut state = PetDisplayRecoveryState::default();
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::None
        );
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::None
        );
    }

    #[test]
    fn real_off_to_on_transition_starts_one_recovery() {
        let mut state = PetDisplayRecoveryState::default();
        assert_eq!(
            state.observe_display_state(SessionDisplayState::Off),
            DisplayObservation::None
        );
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::BeginRecovery { generation: 1 }
        );
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::None
        );
        assert_eq!(state.complete(1), Some(VisibilityCommand::Show));
    }

    #[test]
    fn user_hidden_pet_is_not_reshown_after_resume() {
        let mut state = PetDisplayRecoveryState::default();
        assert_eq!(state.set_desired_visible(false), VisibilityCommand::Hide);
        state.observe_display_state(SessionDisplayState::Off);
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::None
        );
    }

    #[test]
    fn later_off_transition_cancels_stale_recovery() {
        let mut state = PetDisplayRecoveryState::default();
        state.observe_display_state(SessionDisplayState::Off);
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::BeginRecovery { generation: 1 }
        );
        state.observe_display_state(SessionDisplayState::Dimmed);
        assert_eq!(state.complete(1), None);
    }

    #[test]
    fn user_hide_during_recovery_cancels_the_pending_restore() {
        let mut state = PetDisplayRecoveryState::default();
        state.observe_display_state(SessionDisplayState::Off);
        state.observe_display_state(SessionDisplayState::On);

        assert_eq!(state.set_desired_visible(false), VisibilityCommand::Hide);
        assert_eq!(state.complete(1), None);
    }

    #[test]
    fn explicit_tray_toggle_can_reveal_a_safety_hidden_pet() {
        let mut state = PetDisplayRecoveryState::default();
        state.observe_display_state(SessionDisplayState::Off);
        state.observe_display_state(SessionDisplayState::On);
        assert!(state.timeout(1));
        assert_eq!(state.toggle_requested(false), VisibilityCommand::Show);
    }

    #[test]
    fn disabled_protection_tracks_display_state_without_starting_recovery() {
        let mut state = PetDisplayRecoveryState::default();
        state.disable_protection();

        assert_eq!(
            state.observe_display_state(SessionDisplayState::Off),
            DisplayObservation::None
        );
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::None
        );
        assert!(state.active_recovery.is_none());
    }

    #[test]
    fn disabling_protection_restores_an_interrupted_visible_pet() {
        let mut state = PetDisplayRecoveryState::default();
        state.observe_display_state(SessionDisplayState::Off);
        state.observe_display_state(SessionDisplayState::On);

        assert_eq!(state.disable_protection(), Some(VisibilityCommand::Show));
        assert!(state.active_recovery.is_none());
        assert!(state.safety_suppressed.is_none());
    }

    #[test]
    fn gpu_recovery_is_not_cancelled_by_the_display_protection_setting() {
        let mut state = PetDisplayRecoveryState::default();
        let generation = state.begin_gpu_process_recovery();

        assert_eq!(state.disable_protection(), None);
        assert!(state.is_current(generation));
        assert_eq!(state.complete(generation), Some(VisibilityCommand::Show));
    }

    #[test]
    fn gpu_recovery_does_not_present_a_user_hidden_pet() {
        let mut state = PetDisplayRecoveryState::default();
        state.set_desired_visible(false);
        let generation = state.begin_gpu_process_recovery();

        assert!(!state.should_present(generation));
        assert_eq!(state.complete(generation), Some(VisibilityCommand::Hide));
    }

    #[test]
    fn gpu_recovery_supersedes_an_active_display_resume_recovery() {
        let mut state = PetDisplayRecoveryState::default();
        state.observe_display_state(SessionDisplayState::Off);
        assert_eq!(
            state.observe_display_state(SessionDisplayState::On),
            DisplayObservation::BeginRecovery { generation: 1 }
        );

        let gpu_generation = state.begin_gpu_process_recovery();

        assert_eq!(gpu_generation, 2);
        assert!(!state.is_current(1));
        assert!(state.is_current(2));
    }

    #[test]
    fn unchanged_topology_is_ready_at_the_minimum_hidden_interval() {
        let mut tracker = TopologyStabilityTracker::default();
        assert!(!tracker.observe(
            topology(1000),
            0,
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_STABLE_MS
        ));
        assert!(tracker.observe(
            topology(1000),
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_STABLE_MS
        ));
    }

    #[test]
    fn late_dpi_change_restarts_the_stability_window() {
        let mut tracker = TopologyStabilityTracker::default();
        assert!(!tracker.observe(
            topology(1000),
            0,
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_STABLE_MS
        ));
        assert!(!tracker.observe(
            topology(1250),
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_STABLE_MS
        ));
        assert!(!tracker.observe(
            topology(1250),
            RECOVERY_MIN_HIDDEN_MS + RECOVERY_STABLE_MS - 1,
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_STABLE_MS
        ));
        assert!(tracker.observe(
            topology(1250),
            RECOVERY_MIN_HIDDEN_MS + RECOVERY_STABLE_MS,
            RECOVERY_MIN_HIDDEN_MS,
            RECOVERY_STABLE_MS
        ));
    }

    #[test]
    fn safety_gate_defaults_on_and_has_an_explicit_emergency_rollback_value() {
        assert!(safety_gate_enabled_from_value(None));
        assert!(safety_gate_enabled_from_value(Some("true")));
        assert!(!safety_gate_enabled_from_value(Some("0")));
        assert!(!safety_gate_enabled_from_value(Some("off")));
    }

    #[test]
    fn gpu_restart_defaults_on_and_has_an_independent_emergency_rollback_value() {
        assert!(gpu_restart_enabled_from_value(None));
        assert!(gpu_restart_enabled_from_value(Some("true")));
        assert!(!gpu_restart_enabled_from_value(Some("0")));
        assert!(!gpu_restart_enabled_from_value(Some("disabled")));
    }

    #[test]
    fn gpu_restart_guard_allows_one_restart_per_cooldown() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-gpu-restart-guard-{}-{}",
            std::process::id(),
            unix_time_ms()
        ));
        let path = root.join(GPU_RECOVERY_RESTART_GUARD_FILE);

        assert!(claim_gpu_restart_at(&path, 10_000).unwrap());
        assert!(!claim_gpu_restart_at(&path, 10_001).unwrap());
        assert!(claim_gpu_restart_at(&path, 10_000 + GPU_RECOVERY_RESTART_COOLDOWN_MS).unwrap());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn malformed_gpu_restart_guard_fails_closed() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-gpu-restart-guard-malformed-{}-{}",
            std::process::id(),
            unix_time_ms()
        ));
        fs::create_dir_all(&root).unwrap();
        let path = root.join(GPU_RECOVERY_RESTART_GUARD_FILE);
        fs::write(&path, b"not-json").unwrap();

        assert!(claim_gpu_restart_at(&path, 10_000).is_err());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn transient_presented_probe_errors_retry_before_suppression() {
        let error = Err("presented desktop recovery probe timed out after 750 ms".to_owned());
        assert_eq!(
            decide_presented_probe_action(&error, true, true, 1),
            PresentedProbeAction::Retry
        );
        assert_eq!(
            decide_presented_probe_action(
                &error,
                true,
                true,
                RECOVERY_PRESENTED_PROBE_MAX_ATTEMPTS - 1,
            ),
            PresentedProbeAction::Retry
        );
        assert_eq!(
            decide_presented_probe_action(
                &error,
                true,
                true,
                RECOVERY_PRESENTED_PROBE_MAX_ATTEMPTS,
            ),
            PresentedProbeAction::SuppressError
        );
        assert_eq!(
            decide_presented_probe_action(
                &Err("hidden WebView2 baseline changed before presented capture".to_owned()),
                true,
                true,
                1,
            ),
            PresentedProbeAction::SuppressError
        );
    }

    #[test]
    fn unsafe_or_cancelled_presented_probe_is_never_retried() {
        assert_eq!(
            decide_presented_probe_action(&Ok(false), true, true, 1),
            PresentedProbeAction::SuppressUnsafe
        );
        assert_eq!(
            decide_presented_probe_action(
                &Err("presented desktop recovery probe timed out after 750 ms".to_owned()),
                false,
                true,
                1,
            ),
            PresentedProbeAction::Cancel
        );
    }
}
