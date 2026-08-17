use super::pet_display_topology::DisplayTopologySnapshot;
use std::time::Duration;
use tauri::Manager;

pub const RECOVERY_POLL_INTERVAL_MS: u64 = 250;
pub const RECOVERY_MIN_HIDDEN_MS: u64 = 6_500;
pub const RECOVERY_STABLE_MS: u64 = 1_500;
pub const RECOVERY_TIMEOUT_MS: u64 = 12_000;
pub const RECOVERY_PRESENTED_PROBE_MAX_ATTEMPTS: u32 = 3;
pub const RECOVERY_PRESENTED_PROBE_RETRY_DELAY_MS: u64 = 500;
pub const RECOVERY_SAFETY_GATE_ENV: &str = "AGENT_FRIEND_DISPLAY_RECOVERY_SAFETY_GATE";

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
    active_generation: Option<u64>,
    safety_suppressed: bool,
}

impl Default for PetDisplayRecoveryState {
    fn default() -> Self {
        Self {
            last_display_state: None,
            protection_enabled: true,
            desired_visible: true,
            next_generation: 0,
            active_generation: None,
            safety_suppressed: false,
        }
    }
}

impl PetDisplayRecoveryState {
    pub fn observe_display_state(&mut self, state: SessionDisplayState) -> DisplayObservation {
        let previous = self.last_display_state.replace(state);

        if !self.protection_enabled {
            self.active_generation = None;
            self.safety_suppressed = false;
            return DisplayObservation::None;
        }

        if state.is_not_rendering() {
            self.active_generation = None;
            return DisplayObservation::None;
        }

        if state != SessionDisplayState::On
            || !previous.is_some_and(SessionDisplayState::is_not_rendering)
            || !self.desired_visible
        {
            return DisplayObservation::None;
        }

        self.next_generation = self.next_generation.saturating_add(1);
        self.active_generation = Some(self.next_generation);
        self.safety_suppressed = false;
        DisplayObservation::BeginRecovery {
            generation: self.next_generation,
        }
    }

    pub fn set_desired_visible(&mut self, visible: bool) -> VisibilityCommand {
        self.desired_visible = visible;
        self.active_generation = None;
        self.safety_suppressed = false;
        if visible {
            VisibilityCommand::Show
        } else {
            VisibilityCommand::Hide
        }
    }

    pub fn toggle_requested(&mut self, physically_visible: bool) -> VisibilityCommand {
        if !physically_visible
            && self.desired_visible
            && (self.active_generation.is_some() || self.safety_suppressed)
        {
            self.active_generation = None;
            self.safety_suppressed = false;
            return VisibilityCommand::Show;
        }
        self.set_desired_visible(!self.desired_visible)
    }

    pub fn is_current(&self, generation: u64) -> bool {
        self.active_generation == Some(generation)
    }

    pub fn complete(&mut self, generation: u64) -> Option<VisibilityCommand> {
        if !self.is_current(generation) {
            return None;
        }
        self.active_generation = None;
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
        self.active_generation = None;
        self.safety_suppressed = self.desired_visible;
        true
    }

    pub fn disable_protection(&mut self) -> Option<VisibilityCommand> {
        self.protection_enabled = false;
        let interrupted = self.active_generation.take().is_some() || self.safety_suppressed;
        self.safety_suppressed = false;
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
    pub fn observe(&mut self, snapshot: DisplayTopologySnapshot, elapsed_ms: u64) -> bool {
        if self.last_snapshot.as_ref() != Some(&snapshot) {
            self.last_snapshot = Some(snapshot);
            self.unchanged_since_ms = elapsed_ms;
        }
        elapsed_ms >= RECOVERY_MIN_HIDDEN_MS
            && elapsed_ms.saturating_sub(self.unchanged_since_ms) >= RECOVERY_STABLE_MS
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

fn suppress_recovery(app: &tauri::AppHandle, generation: u64, stage: &'static str, detail: &str) {
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

fn schedule(app: &tauri::AppHandle, generation: u64) {
    let Some(pet) = app.get_webview_window("pet") else {
        return;
    };
    if let Err(error) = pet.hide() {
        log::warn!(
            "pet display recovery: temporary hide failed generation={} error={}",
            generation,
            error
        );
        return;
    }

    log::info!(
        "pet display recovery started: generation={} min_hidden_ms={} stable_ms={} timeout_ms={} safety_gate_enabled={}",
        generation,
        RECOVERY_MIN_HIDDEN_MS,
        RECOVERY_STABLE_MS,
        RECOVERY_TIMEOUT_MS,
        safety_gate_enabled()
    );
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let started = std::time::Instant::now();
        let mut stability = TopologyStabilityTracker::default();
        loop {
            tokio::time::sleep(Duration::from_millis(RECOVERY_POLL_INTERVAL_MS)).await;
            if !is_current(&handle, generation) {
                log::info!("pet display recovery cancelled: generation={}", generation);
                return;
            }

            let elapsed_ms = started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
            let stable = handle
                .get_webview_window("pet")
                .and_then(|window| crate::read_pet_display_topology(&window))
                .is_some_and(|snapshot| stability.observe(snapshot, elapsed_ms));
            if stable {
                let gate_enabled = safety_gate_enabled();
                let Some(manager) = handle
                    .try_state::<std::sync::Arc<crate::webview_diagnostics::WebViewDiagnosticsManager>>()
                    .map(|state| state.inner().clone())
                else {
                    if gate_enabled {
                        suppress_recovery(
                            &handle,
                            generation,
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
                    .capture_display_recovery_baseline(&handle, generation, elapsed_ms)
                    .await
                {
                    Ok(record) => record,
                    Err(error) => {
                        if gate_enabled {
                            suppress_recovery(&handle, generation, "baseline-capture", &error);
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
                        "pet display recovery cancelled after baseline: generation={}",
                        generation
                    );
                    return;
                }
                if let Err(reasons) = manager.validate_display_recovery_baseline(&baseline) {
                    if gate_enabled {
                        suppress_recovery(
                            &handle,
                            generation,
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
                let mut attempt = 1;
                loop {
                    if let Err(error) = apply_visibility_command(
                        &handle,
                        VisibilityCommand::Show,
                        "display-resume-probe",
                    ) {
                        suppress_recovery(&handle, generation, "probe-show", &error);
                        return;
                    }
                    let probe_elapsed_ms =
                        started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64;
                    let presented_safe = manager
                        .capture_display_recovery_presented(
                            &handle,
                            &baseline,
                            generation,
                            probe_elapsed_ms,
                        )
                        .await;
                    match decide_presented_probe_action(
                        &presented_safe,
                        is_current(&handle, generation),
                        gate_enabled,
                        attempt,
                    ) {
                        PresentedProbeAction::Complete => {
                            if complete_and_restore(&handle, generation, "display-resume-complete")
                                .unwrap_or(false)
                            {
                                log::info!(
                                    "pet display recovery completed with render evidence: generation={} elapsed_ms={} attempts={}",
                                    generation,
                                    probe_elapsed_ms,
                                    attempt
                                );
                            }
                        }
                        PresentedProbeAction::Cancel => {
                            log::info!(
                                "pet display recovery cancelled after presented probe: generation={}",
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
                                "display-resume-probe-retry",
                            ) {
                                suppress_recovery(
                                    &handle,
                                    generation,
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
                                    "pet display recovery cancelled during presented probe retry delay: generation={}",
                                    generation
                                );
                                return;
                            }
                            attempt += 1;
                            continue;
                        }
                        PresentedProbeAction::SuppressUnsafe => suppress_recovery(
                            &handle,
                            generation,
                            "presented-validation",
                            "presented desktop gained a near-black full-screen layer",
                        ),
                        PresentedProbeAction::SuppressError => {
                            let error = presented_safe
                                .as_ref()
                                .expect_err("suppression is only selected for probe errors");
                            suppress_recovery(&handle, generation, "presented-capture", error);
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

            if elapsed_ms >= RECOVERY_TIMEOUT_MS {
                let suppressed = handle
                    .try_state::<crate::PetOverlayState>()
                    .and_then(|state| {
                        state
                            .inner
                            .lock()
                            .ok()
                            .map(|mut guard| guard.display_recovery.timeout(generation))
                    })
                    .unwrap_or(false);
                if suppressed {
                    log::error!(
                        "pet display recovery timed out: generation={} elapsed_ms={}; pet remains safely hidden and can be revealed from the tray",
                        generation,
                        elapsed_ms
                    );
                }
                return;
            }
        }
    });
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
        schedule(app, generation);
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
        assert!(state.active_generation.is_none());
    }

    #[test]
    fn disabling_protection_restores_an_interrupted_visible_pet() {
        let mut state = PetDisplayRecoveryState::default();
        state.observe_display_state(SessionDisplayState::Off);
        state.observe_display_state(SessionDisplayState::On);

        assert_eq!(state.disable_protection(), Some(VisibilityCommand::Show));
        assert!(state.active_generation.is_none());
        assert!(!state.safety_suppressed);
    }

    #[test]
    fn unchanged_topology_is_ready_at_the_minimum_hidden_interval() {
        let mut tracker = TopologyStabilityTracker::default();
        assert!(!tracker.observe(topology(1000), 0));
        assert!(tracker.observe(topology(1000), RECOVERY_MIN_HIDDEN_MS));
    }

    #[test]
    fn late_dpi_change_restarts_the_stability_window() {
        let mut tracker = TopologyStabilityTracker::default();
        assert!(!tracker.observe(topology(1000), 0));
        assert!(!tracker.observe(topology(1250), RECOVERY_MIN_HIDDEN_MS));
        assert!(!tracker.observe(
            topology(1250),
            RECOVERY_MIN_HIDDEN_MS + RECOVERY_STABLE_MS - 1
        ));
        assert!(tracker.observe(topology(1250), RECOVERY_MIN_HIDDEN_MS + RECOVERY_STABLE_MS));
    }

    #[test]
    fn safety_gate_defaults_on_and_has_an_explicit_emergency_rollback_value() {
        assert!(safety_gate_enabled_from_value(None));
        assert!(safety_gate_enabled_from_value(Some("true")));
        assert!(!safety_gate_enabled_from_value(Some("0")));
        assert!(!safety_gate_enabled_from_value(Some("off")));
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
