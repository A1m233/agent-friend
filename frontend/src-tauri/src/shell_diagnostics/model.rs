use serde::Serialize;

pub(crate) const SHELL_DIAGNOSTICS_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RawShellEvent {
    pub received_at: String,
    pub source: String,
    pub kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub event_id: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hwnd: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub app_window_label: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub object_id: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub child_id: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub event_thread_id: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub event_uptime_ms: Option<u32>,
}

impl RawShellEvent {
    pub(crate) fn lifecycle(kind: impl Into<String>) -> Self {
        Self {
            received_at: now_rfc3339(),
            source: "lifecycle".into(),
            kind: kind.into(),
            event_id: None,
            hwnd: None,
            app_window_label: None,
            object_id: None,
            child_id: None,
            event_thread_id: None,
            event_uptime_ms: None,
        }
    }

    pub(crate) fn tauri(
        kind: impl Into<String>,
        hwnd: Option<usize>,
        app_window_label: impl Into<String>,
    ) -> Self {
        Self {
            received_at: now_rfc3339(),
            source: "tauri-window".into(),
            kind: kind.into(),
            event_id: None,
            hwnd: hwnd.map(format_hwnd),
            app_window_label: Some(app_window_label.into()),
            object_id: None,
            child_id: None,
            event_thread_id: None,
            event_uptime_ms: None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RectSnapshot {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl RectSnapshot {
    pub(crate) fn covers(self, target: Self, tolerance: i32) -> bool {
        self.left <= target.left + tolerance
            && self.top <= target.top + tolerance
            && self.right >= target.right - tolerance
            && self.bottom >= target.bottom - tolerance
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WindowSnapshot {
    pub hwnd: String,
    pub roles: Vec<String>,
    pub process_id: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub process_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub class_name: Option<String>,
    pub visible: bool,
    pub minimized: bool,
    pub maximized: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cloaked: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub window_rect: Option<RectSnapshot>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub extended_frame_rect: Option<RectSnapshot>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub monitor_rect: Option<RectSnapshot>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub monitor_work_rect: Option<RectSnapshot>,
    pub style: String,
    pub extended_style: String,
    pub topmost: bool,
    pub layered: bool,
    pub transparent: bool,
    pub no_activate: bool,
    pub tool_window: bool,
    pub non_rude_property_present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub z_order_rank: Option<u32>,
    pub fullscreen_geometry_candidate: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ShellSnapshot {
    pub schema_version: u32,
    pub captured_at: String,
    pub capability: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub foreground_hwnd: Option<String>,
    pub windows: Vec<WindowSnapshot>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub taskbar_auto_hide: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub taskbar_always_on_top: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub taskbar_above_foreground_fullscreen: Option<bool>,
    pub probe_errors: Vec<String>,
}

impl ShellSnapshot {
    pub(crate) fn unsupported() -> Self {
        Self {
            schema_version: SHELL_DIAGNOSTICS_SCHEMA_VERSION,
            captured_at: now_rfc3339(),
            capability: "unsupported",
            foreground_hwnd: None,
            windows: Vec::new(),
            taskbar_auto_hide: None,
            taskbar_always_on_top: None,
            taskbar_above_foreground_fullscreen: None,
            probe_errors: Vec::new(),
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ShellJournalRecord {
    pub schema_version: u32,
    pub sequence: u64,
    pub process_session_id: String,
    pub recorded_at: String,
    pub events: Vec<RawShellEvent>,
    pub truncated_event_count: u64,
    pub dropped_event_count: u64,
    pub snapshot: ShellSnapshot,
}

#[derive(Debug)]
pub(crate) struct ShellDiagnosticArtifact {
    pub name: String,
    pub bytes: Vec<u8>,
}

#[derive(Debug)]
pub(crate) struct ShellDiagnosticsExport {
    pub supported: bool,
    pub artifacts: Vec<ShellDiagnosticArtifact>,
    pub failures: Vec<String>,
}

pub(crate) fn now_rfc3339() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
}

pub(crate) fn format_hwnd(hwnd: usize) -> String {
    format!("0x{hwnd:x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fullscreen_geometry_allows_small_frame_rounding_difference() {
        let monitor = RectSnapshot {
            left: 0,
            top: 0,
            right: 2560,
            bottom: 1440,
        };
        let rounded = RectSnapshot {
            left: 1,
            top: 0,
            right: 2559,
            bottom: 1440,
        };

        assert!(rounded.covers(monitor, 2));
        assert!(!rounded.covers(monitor, 0));
    }
}
