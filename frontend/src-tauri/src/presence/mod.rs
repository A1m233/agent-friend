use serde::Serialize;
use tauri::WebviewWindow;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopPresenceProbe {
    window_visible: bool,
    window_focused: bool,
    window_minimized: bool,
    os_idle_ms: Option<u64>,
    session_state: &'static str,
    capabilities: Vec<&'static str>,
}

#[tauri::command]
pub fn desktop_presence_probe(window: WebviewWindow) -> Result<DesktopPresenceProbe, String> {
    let window_visible = window.is_visible().map_err(|error| error.to_string())?;
    let window_focused = window.is_focused().map_err(|error| error.to_string())?;
    let window_minimized = window.is_minimized().map_err(|error| error.to_string())?;
    let platform = platform_presence(window_focused);
    Ok(DesktopPresenceProbe {
        window_visible,
        window_focused,
        window_minimized,
        os_idle_ms: platform.idle_ms,
        session_state: platform.session_state,
        capabilities: platform.capabilities,
    })
}

struct PlatformPresence {
    idle_ms: Option<u64>,
    session_state: &'static str,
    capabilities: Vec<&'static str>,
}

#[cfg(target_os = "windows")]
fn platform_presence(_window_focused: bool) -> PlatformPresence {
    windows::probe()
}

#[cfg(target_os = "macos")]
fn platform_presence(window_focused: bool) -> PlatformPresence {
    macos::probe(window_focused)
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn platform_presence(_window_focused: bool) -> PlatformPresence {
    PlatformPresence {
        idle_ms: None,
        session_state: "unknown",
        capabilities: vec!["window"],
    }
}

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "windows")]
mod windows;
