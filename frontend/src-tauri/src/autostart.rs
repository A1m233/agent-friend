#[cfg(all(target_os = "windows", not(debug_assertions)))]
use crate::install_contract;
use serde::Serialize;
use tauri::{AppHandle, Runtime, WebviewWindow};

#[cfg(all(target_os = "windows", any(test, not(debug_assertions))))]
use std::path::Path;

#[cfg(all(target_os = "windows", not(debug_assertions)))]
use tauri_plugin_autostart::ManagerExt;
#[cfg(all(target_os = "windows", not(debug_assertions)))]
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

const MAIN_WORKSPACE_WINDOW: &str = "chat";
#[cfg(all(target_os = "windows", not(debug_assertions)))]
const WINDOWS_RUN_KEY: &str = "Software\\Microsoft\\Windows\\CurrentVersion\\Run";
#[cfg(all(target_os = "windows", not(debug_assertions)))]
fn windows_run_value() -> String {
    install_contract::embedded_profile()
        .map(|profile| profile.autostart_value_name)
        .unwrap_or_else(|_| "agent-friend".to_string())
}

#[cfg(all(target_os = "windows", any(test, not(debug_assertions))))]
fn windows_autostart_command(executable: &Path) -> String {
    format!("\"{}\" --autostart", executable.display())
}

#[cfg(all(target_os = "windows", not(debug_assertions)))]
fn normalize_windows_run_entry() -> Result<(), String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let run = hkcu
        .open_subkey_with_flags(WINDOWS_RUN_KEY, winreg::enums::KEY_SET_VALUE)
        .map_err(|error| error.to_string())?;
    run.set_value(windows_run_value(), &windows_autostart_command(&executable))
        .map_err(|error| error.to_string())
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AutostartStatus {
    pub supported: bool,
    pub enabled: bool,
    pub reason: Option<String>,
}

impl AutostartStatus {
    #[cfg(any(test, all(target_os = "windows", not(debug_assertions))))]
    fn supported(enabled: bool) -> Self {
        Self {
            supported: true,
            enabled,
            reason: None,
        }
    }

    #[cfg(any(not(target_os = "windows"), debug_assertions))]
    fn unavailable(reason: &str) -> Self {
        Self {
            supported: false,
            enabled: false,
            reason: Some(reason.to_string()),
        }
    }
}

#[cfg(any(test, all(target_os = "windows", not(debug_assertions))))]
trait AutostartBackend {
    fn is_enabled(&self) -> Result<bool, String>;
    fn enable(&self) -> Result<(), String>;
    fn disable(&self) -> Result<(), String>;
}

fn ensure_main_workspace_window(label: &str) -> Result<(), String> {
    if label == MAIN_WORKSPACE_WINDOW {
        Ok(())
    } else {
        Err("autostart_forbidden: only the main workspace may manage autostart".to_string())
    }
}

#[cfg(any(test, all(target_os = "windows", not(debug_assertions))))]
fn query_backend(backend: &impl AutostartBackend) -> Result<AutostartStatus, String> {
    backend
        .is_enabled()
        .map(AutostartStatus::supported)
        .map_err(|error| format!("autostart_query_failed: {error}"))
}

#[cfg(any(test, all(target_os = "windows", not(debug_assertions))))]
fn set_backend(backend: &impl AutostartBackend, enabled: bool) -> Result<AutostartStatus, String> {
    let operation = if enabled {
        backend.enable()
    } else {
        backend.disable()
    };
    operation.map_err(|error| format!("autostart_update_failed: {error}"))?;
    let status = query_backend(backend)?;
    if status.enabled != enabled {
        return Err(format!(
            "autostart_state_mismatch: requested {enabled}, actual {}",
            status.enabled
        ));
    }
    Ok(status)
}

#[cfg(all(target_os = "windows", not(debug_assertions)))]
struct PluginBackend<'a, R: Runtime>(&'a AppHandle<R>);

#[cfg(all(target_os = "windows", not(debug_assertions)))]
impl<R: Runtime> AutostartBackend for PluginBackend<'_, R> {
    fn is_enabled(&self) -> Result<bool, String> {
        self.0
            .autolaunch()
            .is_enabled()
            .map_err(|error| error.to_string())
    }

    fn enable(&self) -> Result<(), String> {
        self.0
            .autolaunch()
            .enable()
            .map_err(|error| error.to_string())?;
        if let Err(error) = normalize_windows_run_entry() {
            let rollback = self.0.autolaunch().disable();
            return Err(match rollback {
                Ok(()) => format!("failed to normalize the Windows startup command: {error}"),
                Err(rollback_error) => format!(
                    "failed to normalize the Windows startup command: {error}; rollback failed: {rollback_error}"
                ),
            });
        }
        Ok(())
    }

    fn disable(&self) -> Result<(), String> {
        self.0
            .autolaunch()
            .disable()
            .map_err(|error| error.to_string())
    }
}

#[tauri::command]
pub fn get_autostart_status<R: Runtime>(
    window: WebviewWindow<R>,
    app: AppHandle<R>,
) -> Result<AutostartStatus, String> {
    ensure_main_workspace_window(window.label())?;

    #[cfg(all(target_os = "windows", not(debug_assertions)))]
    {
        return query_backend(&PluginBackend(&app));
    }

    #[cfg(any(not(target_os = "windows"), debug_assertions))]
    {
        let _ = app;
        Ok(AutostartStatus::unavailable(
            "开机自启只能在正式安装版中设置",
        ))
    }
}

#[tauri::command]
pub fn set_autostart_enabled<R: Runtime>(
    window: WebviewWindow<R>,
    app: AppHandle<R>,
    enabled: bool,
) -> Result<AutostartStatus, String> {
    ensure_main_workspace_window(window.label())?;

    #[cfg(all(target_os = "windows", not(debug_assertions)))]
    {
        return set_backend(&PluginBackend(&app), enabled);
    }

    #[cfg(any(not(target_os = "windows"), debug_assertions))]
    {
        let _ = (app, enabled);
        Err("autostart_debug_disabled: 开机自启只能在正式安装版中设置".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::Cell, rc::Rc};

    struct FakeBackend {
        enabled: Rc<Cell<bool>>,
        fail: bool,
        ignore_writes: bool,
    }

    impl AutostartBackend for FakeBackend {
        fn is_enabled(&self) -> Result<bool, String> {
            if self.fail {
                Err("query denied".to_string())
            } else {
                Ok(self.enabled.get())
            }
        }

        fn enable(&self) -> Result<(), String> {
            if self.fail {
                return Err("write denied".to_string());
            }
            if !self.ignore_writes {
                self.enabled.set(true);
            }
            Ok(())
        }

        fn disable(&self) -> Result<(), String> {
            if self.fail {
                return Err("write denied".to_string());
            }
            if !self.ignore_writes {
                self.enabled.set(false);
            }
            Ok(())
        }
    }

    fn fake(initial: bool) -> FakeBackend {
        FakeBackend {
            enabled: Rc::new(Cell::new(initial)),
            fail: false,
            ignore_writes: false,
        }
    }

    #[test]
    fn only_main_workspace_can_manage_autostart() {
        assert!(ensure_main_workspace_window("chat").is_ok());
        assert!(ensure_main_workspace_window("pet")
            .unwrap_err()
            .starts_with("autostart_forbidden:"));
    }

    #[test]
    fn query_and_update_return_actual_system_state() {
        let backend = fake(false);
        assert_eq!(
            query_backend(&backend).unwrap(),
            AutostartStatus::supported(false)
        );
        assert_eq!(
            set_backend(&backend, true).unwrap(),
            AutostartStatus::supported(true)
        );
        assert_eq!(
            set_backend(&backend, false).unwrap(),
            AutostartStatus::supported(false)
        );
    }

    #[test]
    fn backend_errors_are_structured() {
        let backend = FakeBackend {
            fail: true,
            ..fake(false)
        };
        assert!(query_backend(&backend)
            .unwrap_err()
            .starts_with("autostart_query_failed:"));
        assert!(set_backend(&backend, true)
            .unwrap_err()
            .starts_with("autostart_update_failed:"));
    }

    #[test]
    fn successful_write_is_verified_against_actual_state() {
        let backend = FakeBackend {
            ignore_writes: true,
            ..fake(false)
        };
        assert!(set_backend(&backend, true)
            .unwrap_err()
            .starts_with("autostart_state_mismatch:"));
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_startup_command_quotes_paths_with_spaces() {
        let executable = std::path::Path::new(r"D:\Apps with spaces\agent-friend.exe");
        assert_eq!(
            windows_autostart_command(executable),
            r#""D:\Apps with spaces\agent-friend.exe" --autostart"#
        );
    }
}
