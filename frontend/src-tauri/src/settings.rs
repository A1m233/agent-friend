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
    #[serde(default = "default_deepseek_model")]
    pub deepseek_model: DeepseekModel,
    #[serde(default = "default_deepseek_memory_model")]
    pub deepseek_memory_model: DeepseekModel,
    #[serde(default = "default_avatar_model_id")]
    pub avatar_model_id: String,
    #[serde(default)]
    pub pet_display_mode: PetDisplayMode,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ThemeMode {
    Light,
    Dark,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub enum DeepseekModel {
    #[serde(rename = "deepseek/deepseek-v4-flash")]
    Flash,
    #[serde(rename = "deepseek/deepseek-v4-pro")]
    Pro,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum PetDisplayMode {
    #[default]
    Normal,
    EdgeRecall,
}

impl PetDisplayMode {
    pub fn attr(&self) -> &'static str {
        match self {
            PetDisplayMode::Normal => "normal",
            PetDisplayMode::EdgeRecall => "edgeRecall",
        }
    }
}

impl Default for DeepseekModel {
    fn default() -> Self {
        default_deepseek_model()
    }
}

fn default_deepseek_model() -> DeepseekModel {
    DeepseekModel::Flash
}

fn default_deepseek_memory_model() -> DeepseekModel {
    DeepseekModel::Pro
}

fn default_avatar_model_id() -> String {
    "builtin:live2d:hiyori".to_string()
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            theme: ThemeMode::Light,
            voice_tunnel_consent_accepted: false,
            deepseek_model: default_deepseek_model(),
            deepseek_memory_model: default_deepseek_memory_model(),
            avatar_model_id: default_avatar_model_id(),
            pet_display_mode: PetDisplayMode::Normal,
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
    presence: SettingsPresence,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct SettingsPresence {
    deepseek_memory_model: bool,
}

fn parse_settings(raw: &str, path: &Path) -> Result<(Settings, SettingsPresence), String> {
    let value = serde_json::from_str::<serde_json::Value>(raw)
        .map_err(|e| format!("settings: parse {path:?} failed: {e}"))?;
    let inner = value.get("settings").unwrap_or(&value);
    let presence = SettingsPresence {
        deepseek_memory_model: inner.get("deepseekMemoryModel").is_some(),
    };
    let settings = serde_json::from_value::<Settings>(inner.clone())
        .map_err(|e| format!("settings: parse {path:?} failed: {e}"))?;
    Ok((settings, presence))
}

#[cfg(test)]
fn read_settings_file(path: &Path) -> Result<Settings, String> {
    let raw =
        fs::read_to_string(path).map_err(|e| format!("settings: read {path:?} failed: {e}"))?;
    parse_settings(&raw, path).map(|(settings, _)| settings)
}

fn read_settings_file_with_presence(path: &Path) -> Result<(Settings, SettingsPresence), String> {
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

fn parse_deepseek_model(raw: &str) -> Option<DeepseekModel> {
    match raw.trim() {
        "deepseek/deepseek-v4-flash" => Some(DeepseekModel::Flash),
        "deepseek/deepseek-v4-pro" => Some(DeepseekModel::Pro),
        _ => None,
    }
}

fn dotenv_value(path: &Path, name: &str) -> Option<String> {
    let raw = fs::read_to_string(path).ok()?;
    raw.lines().find_map(|line| {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            return None;
        }
        let (key, value) = trimmed.split_once('=')?;
        let key = key.trim().strip_prefix("export ").unwrap_or(key.trim());
        if key != name {
            return None;
        }
        let value = value.trim().trim_matches(&['"', '\''][..]).trim();
        if value.is_empty() {
            None
        } else {
            Some(value.to_string())
        }
    })
}

fn looks_like_agent_friend_root(path: &Path) -> bool {
    path.join(".env.example").is_file()
        && path.join("pyproject.toml").is_file()
        && path.join("frontend").is_dir()
}

fn find_repo_dotenv_from(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|candidate| looks_like_agent_friend_root(candidate))
        .map(|root| root.join(".env"))
}

fn legacy_deepseek_memory_model() -> Option<DeepseekModel> {
    const NAME: &str = "DEEPSEEK_MEMORY_MODEL";
    if let Ok(value) = std::env::var(NAME) {
        if let Some(model) = parse_deepseek_model(&value) {
            return Some(model);
        }
    }

    let mut paths = Vec::new();
    if let Ok(root) = std::env::var("AGENT_FRIEND_REPO_ROOT") {
        paths.push(PathBuf::from(root).join(".env"));
    }
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(path) = find_repo_dotenv_from(&cwd) {
            paths.push(path);
        }
    }
    #[cfg(debug_assertions)]
    {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        if let Some(path) = find_repo_dotenv_from(&manifest_dir) {
            paths.push(path);
        }
    }

    paths.dedup();
    paths
        .iter()
        .filter_map(|path| dotenv_value(path, NAME))
        .find_map(|value| parse_deepseek_model(&value))
}

fn migrate_deepseek_memory_model(
    settings: &mut Settings,
    presence: SettingsPresence,
    legacy_model: Option<DeepseekModel>,
) -> bool {
    if presence.deepseek_memory_model {
        return false;
    }
    let Some(model) = legacy_model else {
        return false;
    };
    settings.deepseek_memory_model = model;
    true
}

fn load_from_paths(canonical: Option<PathBuf>, legacy_local: Option<PathBuf>) -> LoadedSettings {
    let mut loaded = if let Some(path) = canonical.as_ref() {
        match read_settings_file_with_presence(path) {
            Ok((settings, presence)) => LoadedSettings {
                settings,
                source: SettingsLoadSource::Canonical,
                path: Some(path.clone()),
                presence,
            },
            Err(e) => {
                log::info!("{e}");
                LoadedSettings {
                    settings: Settings::default(),
                    source: SettingsLoadSource::Default,
                    path: None,
                    presence: SettingsPresence::default(),
                }
            }
        }
    } else {
        log::warn!("settings: cannot resolve canonical settings store path");
        LoadedSettings {
            settings: Settings::default(),
            source: SettingsLoadSource::Default,
            path: None,
            presence: SettingsPresence::default(),
        }
    };

    if loaded.path.is_none() {
        if let Some(path) = legacy_local.as_ref() {
            if canonical.as_ref() != Some(path) {
                match read_settings_file_with_presence(path) {
                    Ok((settings, presence)) => {
                        loaded = LoadedSettings {
                            settings,
                            source: SettingsLoadSource::LegacyLocal,
                            path: Some(path.clone()),
                            presence,
                        };
                    }
                    Err(e) => log::info!("{e}"),
                }
            }
        }
    }

    let migrated_memory_model = migrate_deepseek_memory_model(
        &mut loaded.settings,
        loaded.presence,
        legacy_deepseek_memory_model(),
    );

    if (loaded.source == SettingsLoadSource::LegacyLocal || migrated_memory_model)
        && canonical.is_some()
    {
        let canonical_path = canonical.as_ref().expect("checked");
        match write_settings_file(canonical_path, &loaded.settings) {
            Ok(()) => {
                if loaded.source == SettingsLoadSource::LegacyLocal {
                    log::info!(
                        "settings: migrated legacy settings from {:?} to {:?}",
                        loaded.path,
                        canonical_path
                    );
                }
                if migrated_memory_model {
                    log::info!("settings: migrated legacy DEEPSEEK_MEMORY_MODEL into settings");
                }
            }
            Err(e) => log::warn!("{e}"),
        }
    }

    loaded
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
        "settings: bootstrap resolved source={:?} path={:?} theme={} voiceTunnelConsentAccepted={} avatarModelId={} petDisplayMode={}",
        loaded.source,
        loaded.path,
        loaded.settings.theme_attr(),
        loaded.settings.voice_tunnel_consent_accepted,
        loaded.settings.avatar_model_id,
        loaded.settings.pet_display_mode.attr()
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
        "deepseekModel" => {
            let model: DeepseekModel = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid deepseekModel: {e}"))?;
            s.deepseek_model = model;
        }
        "deepseekMemoryModel" => {
            let model: DeepseekModel = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid deepseekMemoryModel: {e}"))?;
            s.deepseek_memory_model = model;
        }
        "avatarModelId" => {
            let model_id: String = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid avatarModelId: {e}"))?;
            s.avatar_model_id = model_id;
        }
        "petDisplayMode" => {
            let mode: PetDisplayMode = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid petDisplayMode: {e}"))?;
            s.pet_display_mode = mode;
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
        assert_eq!(loaded.settings.avatar_model_id, default_avatar_model_id());
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

    #[test]
    fn default_models_use_flash_for_chat_and_pro_for_memory() {
        let settings = Settings::default();

        assert_eq!(settings.deepseek_model, DeepseekModel::Flash);
        assert_eq!(settings.deepseek_memory_model, DeepseekModel::Pro);
    }

    #[test]
    fn default_avatar_model_id_uses_builtin_hiyori() {
        let settings = Settings::default();

        assert_eq!(settings.avatar_model_id, "builtin:live2d:hiyori");
    }

    #[test]
    fn default_pet_display_mode_is_normal() {
        let settings = Settings::default();

        assert_eq!(settings.pet_display_mode, PetDisplayMode::Normal);
    }

    #[test]
    fn parse_settings_preserves_pet_display_mode() {
        let dir = temp_settings_dir("pet-display-mode");
        let path = dir.join("settings.json");
        write_raw(
            &path,
            r#"{
  "settings": {
    "theme": "light",
    "petDisplayMode": "edgeRecall"
  }
}"#,
        );

        let settings = read_settings_file(&path).expect("read settings");

        assert_eq!(settings.pet_display_mode, PetDisplayMode::EdgeRecall);
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn migrate_deepseek_memory_model_fills_missing_legacy_value() {
        let mut settings = Settings::default();

        let changed = migrate_deepseek_memory_model(
            &mut settings,
            SettingsPresence {
                deepseek_memory_model: false,
            },
            Some(DeepseekModel::Flash),
        );

        assert!(changed);
        assert_eq!(settings.deepseek_memory_model, DeepseekModel::Flash);
    }

    #[test]
    fn migrate_deepseek_memory_model_does_not_override_existing_setting() {
        let mut settings = Settings {
            deepseek_memory_model: DeepseekModel::Pro,
            ..Settings::default()
        };

        let changed = migrate_deepseek_memory_model(
            &mut settings,
            SettingsPresence {
                deepseek_memory_model: true,
            },
            Some(DeepseekModel::Flash),
        );

        assert!(!changed);
        assert_eq!(settings.deepseek_memory_model, DeepseekModel::Pro);
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
mod init_script_tests {
    use super::{build_init_script, Settings, ThemeMode};

    #[test]
    fn build_init_script_sets_tdesign_dark_theme_mode() {
        let script = build_init_script(&Settings {
            theme: ThemeMode::Dark,
            ..Settings::default()
        });

        assert!(script.contains("document.documentElement.setAttribute('theme', 'dark');"));
        assert!(script.contains("document.documentElement.setAttribute('theme-mode', 'dark');"));
    }

    #[test]
    fn build_init_script_clears_tdesign_theme_mode_for_light() {
        let script = build_init_script(&Settings {
            theme: ThemeMode::Light,
            ..Settings::default()
        });

        assert!(script.contains("document.documentElement.setAttribute('theme', 'light');"));
        assert!(script.contains("document.documentElement.removeAttribute('theme-mode');"));
    }

    #[test]
    fn build_init_script_injects_avatar_model_and_pet_display_mode() {
        let script = build_init_script(&Settings {
            avatar_model_id: "builtin:spine:ark-models:103_angel".to_string(),
            pet_display_mode: super::PetDisplayMode::EdgeRecall,
            ..Settings::default()
        });

        assert!(script.contains("\"avatarModelId\":\"builtin:spine:ark-models:103_angel\""));
        assert!(script.contains("\"petDisplayMode\":\"edgeRecall\""));
    }
}
