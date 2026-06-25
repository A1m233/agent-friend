use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tauri::{
    plugin::{Builder as PluginBuilder, TauriPlugin},
    AppHandle, Emitter, Runtime,
};
use tauri_plugin_store::StoreExt;

use crate::paths;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub theme: ThemeMode,
    #[serde(default)]
    pub voice_tunnel_consent_accepted: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ThemeMode {
    Light,
    Dark,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            theme: ThemeMode::Light,
            voice_tunnel_consent_accepted: false,
        }
    }
}

impl Settings {
    pub fn theme_attr(&self) -> &'static str {
        match self.theme {
            ThemeMode::Light => "light",
            ThemeMode::Dark => "dark",
        }
    }

    fn tdesign_theme_mode_script(&self) -> &'static str {
        match self.theme {
            ThemeMode::Light => "document.documentElement.removeAttribute('theme-mode');",
            ThemeMode::Dark => "document.documentElement.setAttribute('theme-mode', 'dark');",
        }
    }
}

const EVENT_CHANGED: &str = "settings://changed";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SettingsLoadSource {
    Canonical,
    LegacyLocal,
    Default,
}

struct LoadedSettings {
    settings: Settings,
    source: SettingsLoadSource,
    path: Option<PathBuf>,
}

fn parse_settings(raw: &str, path: &Path) -> Result<Settings, String> {
    serde_json::from_str::<serde_json::Value>(raw)
        .and_then(|v| match v.get("settings") {
            Some(inner) => serde_json::from_value::<Settings>(inner.clone()),
            None => serde_json::from_value::<Settings>(v),
        })
        .map_err(|e| format!("settings: parse {path:?} failed: {e}"))
}

fn read_settings_file(path: &Path) -> Result<Settings, String> {
    let raw =
        fs::read_to_string(path).map_err(|e| format!("settings: read {path:?} failed: {e}"))?;
    parse_settings(&raw, path)
}

fn write_settings_file(path: &Path, settings: &Settings) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("settings: create parent {parent:?} failed: {e}"))?;
    }
    let payload = serde_json::json!({ "settings": settings });
    let raw = serde_json::to_string_pretty(&payload)
        .map_err(|e| format!("settings: serialize {path:?} failed: {e}"))?;
    fs::write(path, raw).map_err(|e| format!("settings: write {path:?} failed: {e}"))
}

fn load_from_paths(canonical: Option<PathBuf>, legacy_local: Option<PathBuf>) -> LoadedSettings {
    if let Some(path) = canonical.as_ref() {
        match read_settings_file(path) {
            Ok(settings) => {
                return LoadedSettings {
                    settings,
                    source: SettingsLoadSource::Canonical,
                    path: Some(path.clone()),
                };
            }
            Err(e) => log::info!("{e}"),
        }
    } else {
        log::warn!("settings: cannot resolve canonical settings store path");
    }

    if let Some(path) = legacy_local.as_ref() {
        if canonical.as_ref() == Some(path) {
            return LoadedSettings {
                settings: Settings::default(),
                source: SettingsLoadSource::Default,
                path: None,
            };
        }

        match read_settings_file(path) {
            Ok(settings) => {
                if let Some(canonical_path) = canonical.as_ref() {
                    match write_settings_file(canonical_path, &settings) {
                        Ok(()) => log::info!(
                            "settings: migrated legacy settings from {:?} to {:?}",
                            path,
                            canonical_path
                        ),
                        Err(e) => log::warn!("{e}"),
                    }
                }
                return LoadedSettings {
                    settings,
                    source: SettingsLoadSource::LegacyLocal,
                    path: Some(path.clone()),
                };
            }
            Err(e) => log::info!("{e}"),
        }
    }

    LoadedSettings {
        settings: Settings::default(),
        source: SettingsLoadSource::Default,
        path: None,
    }
}

/// 028 · 用统一 paths 模块拼出 tauri-plugin-store settings 文件路径。
///
/// 当前项目沿用 `dirs::config_dir() / bundle_id / settings.json`（Windows 上对应
/// `%APPDATA%/com.agentfriend.desktop/settings.json`）。
/// 启动期（plugin builder 阶段）同步读盘，所有错误路径返回默认值，不阻断启动。
pub fn load_from_disk_or_default() -> Settings {
    let canonical = paths::settings_store_path();
    let legacy_local = paths::legacy_local_settings_store_path();
    log::info!(
        "settings: bootstrap loading canonical={:?} legacy_local={:?}",
        canonical,
        legacy_local
    );

    let loaded = load_from_paths(canonical, legacy_local);
    log::info!(
        "settings: bootstrap resolved source={:?} path={:?} theme={} voiceTunnelConsentAccepted={}",
        loaded.source,
        loaded.path,
        loaded.settings.theme_attr(),
        loaded.settings.voice_tunnel_consent_accepted
    );
    loaded.settings
}

/// 把当前 settings 拼成 webview 初始化脚本。会在每个 webview 加载 HTML 之前同步执行，
/// 保证首帧主题正确、window.__AGENT_FRIEND_SETTINGS__ 立即可用。
pub fn build_init_script(s: &Settings) -> String {
    let json = serde_json::to_string(s).expect("settings serialize");
    format!(
        r#"
        window.__AGENT_FRIEND_SETTINGS__ = {json};
        document.documentElement.setAttribute('theme', '{theme}');
        {tdesign_theme_mode_script}
        "#,
        json = json,
        theme = s.theme_attr(),
        tdesign_theme_mode_script = s.tdesign_theme_mode_script(),
    )
}

/// 启动期已加载的 settings 缓存在 state 中，供 command 快速读取。
fn current_state(state: tauri::State<'_, Arc<Mutex<Settings>>>) -> Result<Settings, String> {
    state
        .lock()
        .map(|s| s.clone())
        .map_err(|e| format!("settings state poisoned: {e}"))
}

#[tauri::command]
pub fn get_setting(state: tauri::State<'_, Arc<Mutex<Settings>>>) -> Result<Settings, String> {
    current_state(state)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetPayload {
    pub key: String,
    pub value: serde_json::Value,
}

#[tauri::command]
pub fn set_setting<R: Runtime>(
    app: AppHandle<R>,
    state: tauri::State<'_, Arc<Mutex<Settings>>>,
    payload: SetPayload,
) -> Result<(), String> {
    let mut s = state
        .lock()
        .map_err(|e| format!("settings state poisoned: {e}"))?;

    match payload.key.as_str() {
        "theme" => {
            let theme: ThemeMode = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid theme: {e}"))?;
            s.theme = theme;
        }
        "voiceTunnelConsentAccepted" => {
            let accepted: bool = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid voiceTunnelConsentAccepted: {e}"))?;
            s.voice_tunnel_consent_accepted = accepted;
        }
        _ => return Err(format!("unknown setting key: {}", payload.key)),
    }

    let store = app
        .store(paths::SETTINGS_STORE_PATH)
        .map_err(|e| e.to_string())?;
    store.set(
        "settings",
        serde_json::to_value(&*s).map_err(|e| e.to_string())?,
    );
    store.save().map_err(|e| e.to_string())?;

    log::info!(
        "settings: store saved to {:?}",
        paths::settings_store_path()
    );

    log::info!(
        "settings: emitting {} key={} value={}",
        EVENT_CHANGED,
        payload.key,
        payload.value
    );

    app.emit(
        EVENT_CHANGED,
        serde_json::json!({ "key": payload.key, "value": payload.value }),
    )
    .map_err(|e| e.to_string())?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_settings_dir(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "agent-friend-settings-{name}-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).expect("create temp settings dir");
        dir
    }

    fn write_raw(path: &Path, raw: &str) {
        fs::create_dir_all(path.parent().expect("parent")).expect("create parent");
        fs::write(path, raw).expect("write raw settings");
    }

    #[test]
    fn load_from_paths_reads_legacy_local_and_migrates_to_canonical() {
        let dir = temp_settings_dir("legacy-migration");
        let canonical = dir.join("roaming").join("settings.json");
        let legacy = dir.join("local").join("settings.json");
        write_raw(
            &legacy,
            r#"{
  "settings": {
    "theme": "dark",
    "voiceTunnelConsentAccepted": true
  }
}"#,
        );

        let loaded = load_from_paths(Some(canonical.clone()), Some(legacy.clone()));

        assert_eq!(loaded.source, SettingsLoadSource::LegacyLocal);
        assert_eq!(loaded.path, Some(legacy));
        assert_eq!(loaded.settings.theme, ThemeMode::Dark);
        assert!(loaded.settings.voice_tunnel_consent_accepted);
        let migrated = read_settings_file(&canonical).expect("read migrated canonical");
        assert_eq!(migrated, loaded.settings);

        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn load_from_paths_prefers_canonical_over_legacy() {
        let dir = temp_settings_dir("canonical-first");
        let canonical = dir.join("roaming").join("settings.json");
        let legacy = dir.join("local").join("settings.json");
        write_raw(
            &canonical,
            r#"{
  "settings": {
    "theme": "light",
    "voiceTunnelConsentAccepted": true
  }
}"#,
        );
        write_raw(
            &legacy,
            r#"{
  "settings": {
    "theme": "dark",
    "voiceTunnelConsentAccepted": false
  }
}"#,
        );

        let loaded = load_from_paths(Some(canonical.clone()), Some(legacy));

        assert_eq!(loaded.source, SettingsLoadSource::Canonical);
        assert_eq!(loaded.path, Some(canonical));
        assert_eq!(loaded.settings.theme, ThemeMode::Light);
        assert!(loaded.settings.voice_tunnel_consent_accepted);

        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn load_from_paths_returns_default_when_no_store_exists() {
        let dir = temp_settings_dir("missing");
        let loaded = load_from_paths(
            Some(dir.join("roaming").join("settings.json")),
            Some(dir.join("local").join("settings.json")),
        );

        assert_eq!(loaded.source, SettingsLoadSource::Default);
        assert_eq!(loaded.settings, Settings::default());

        fs::remove_dir_all(dir).expect("cleanup");
    }
}

/// 028 · settings facade plugin。
/// js_init_script 在 lib.rs 构造 plugin 时由外部传入（启动期同步读盘后拼接）。
/// command 直接注册到 app 级 invoke_handler，避免自定义 plugin 权限文件开销。
pub fn init<R: Runtime>(js_init_script: String) -> TauriPlugin<R> {
    PluginBuilder::new("agent-friend-settings")
        .js_init_script(js_init_script)
        .build()
}

#[cfg(test)]
mod tests {
    use super::{build_init_script, Settings, ThemeMode};

    #[test]
    fn build_init_script_sets_tdesign_dark_theme_mode() {
        let script = build_init_script(&Settings {
            theme: ThemeMode::Dark,
        });

        assert!(script.contains("document.documentElement.setAttribute('theme', 'dark');"));
        assert!(script.contains("document.documentElement.setAttribute('theme-mode', 'dark');"));
    }

    #[test]
    fn build_init_script_clears_tdesign_theme_mode_for_light() {
        let script = build_init_script(&Settings {
            theme: ThemeMode::Light,
        });

        assert!(script.contains("document.documentElement.setAttribute('theme', 'light');"));
        assert!(script.contains("document.documentElement.removeAttribute('theme-mode');"));
    }
}
