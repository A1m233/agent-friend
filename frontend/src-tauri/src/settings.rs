use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tauri::{
    plugin::{Builder as PluginBuilder, TauriPlugin},
    AppHandle, Emitter, Manager, Runtime,
};
use tauri_plugin_store::StoreExt;

use crate::paths;
use crate::power_policy::{BackgroundOnlinePolicy, PowerPolicyManager};

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub theme: ThemeMode,
    #[serde(default)]
    pub background_online_policy: BackgroundOnlinePolicy,
    #[serde(default)]
    pub voice_tunnel_consent_accepted: bool,
    #[serde(default = "default_immersive_mode")]
    pub immersive_mode: bool,
    #[serde(default)]
    pub display_recovery_protection_enabled: bool,
    #[serde(default = "default_chat_model")]
    pub chat_model: ModelSelection,
    #[serde(default = "default_memory_model")]
    pub memory_model: ModelSelection,
    #[serde(default = "default_avatar_model_id")]
    pub avatar_model_id: String,
    #[serde(default)]
    pub pet_avatar_size: PetAvatarSize,
    #[serde(default)]
    pub avatar_model_scale_percents: BTreeMap<String, u16>,
    #[serde(default = "default_pet_mini_scale_percent")]
    pub pet_mini_scale_percent: u16,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ThemeMode {
    Light,
    Dark,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeepseekModel {
    #[serde(rename = "deepseek/deepseek-v4-flash")]
    Flash,
    #[serde(rename = "deepseek/deepseek-v4-pro")]
    Pro,
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ModelProvider {
    Deepseek,
    Kimi,
    Openrouter,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ModelSelection {
    pub provider: ModelProvider,
    pub model: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum PetAvatarSize {
    #[default]
    Normal,
    Mini,
}

impl PetAvatarSize {
    pub fn attr(&self) -> &'static str {
        match self {
            PetAvatarSize::Normal => "normal",
            PetAvatarSize::Mini => "mini",
        }
    }
}

#[derive(Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
enum LegacyPetDisplayMode {
    Normal,
    EdgeRecall,
}

impl Default for DeepseekModel {
    fn default() -> Self {
        default_deepseek_model()
    }
}

fn default_deepseek_model() -> DeepseekModel {
    DeepseekModel::Flash
}

fn deepseek_model_id(model: DeepseekModel) -> String {
    match model {
        DeepseekModel::Flash => "deepseek/deepseek-v4-flash".to_string(),
        DeepseekModel::Pro => "deepseek/deepseek-v4-pro".to_string(),
    }
}

fn default_chat_model() -> ModelSelection {
    ModelSelection {
        provider: ModelProvider::Kimi,
        model: "kimi/kimi-k2.5".to_string(),
    }
}

fn default_memory_model() -> ModelSelection {
    ModelSelection {
        provider: ModelProvider::Deepseek,
        model: "deepseek/deepseek-v4-flash".to_string(),
    }
}

fn model_selection_from_deepseek(model: DeepseekModel) -> ModelSelection {
    ModelSelection {
        provider: ModelProvider::Deepseek,
        model: deepseek_model_id(model),
    }
}

fn default_avatar_model_id() -> String {
    "builtin:live2d:hiyori".to_string()
}

fn default_pet_mini_scale_percent() -> u16 {
    50
}

fn default_immersive_mode() -> bool {
    true
}

fn validate_percent(value: u16, min: u16, max: u16, key: &str) -> Result<u16, String> {
    if value < min || value > max {
        return Err(format!("invalid {key}: expected {min}..={max}"));
    }
    Ok(value)
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            theme: ThemeMode::Light,
            background_online_policy: BackgroundOnlinePolicy::FollowSystem,
            voice_tunnel_consent_accepted: false,
            immersive_mode: default_immersive_mode(),
            display_recovery_protection_enabled: false,
            chat_model: default_chat_model(),
            memory_model: default_memory_model(),
            avatar_model_id: default_avatar_model_id(),
            pet_avatar_size: PetAvatarSize::Normal,
            avatar_model_scale_percents: BTreeMap::new(),
            pet_mini_scale_percent: default_pet_mini_scale_percent(),
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
    chat_model: bool,
    memory_model: bool,
    deepseek_model: Option<DeepseekModel>,
    deepseek_memory_model: Option<DeepseekModel>,
    pet_avatar_size: bool,
    legacy_pet_display_mode: Option<LegacyPetDisplayMode>,
}

fn parse_settings(raw: &str, path: &Path) -> Result<(Settings, SettingsPresence), String> {
    let value = serde_json::from_str::<serde_json::Value>(raw)
        .map_err(|e| format!("settings: parse {path:?} failed: {e}"))?;
    let inner = value.get("settings").unwrap_or(&value);
    let presence = SettingsPresence {
        chat_model: inner.get("chatModel").is_some(),
        memory_model: inner.get("memoryModel").is_some(),
        deepseek_model: inner
            .get("deepseekModel")
            .and_then(|value| serde_json::from_value(value.clone()).ok()),
        deepseek_memory_model: inner
            .get("deepseekMemoryModel")
            .and_then(|value| serde_json::from_value(value.clone()).ok()),
        pet_avatar_size: inner.get("petAvatarSize").is_some(),
        legacy_pet_display_mode: inner
            .get("petDisplayMode")
            .and_then(|value| serde_json::from_value(value.clone()).ok()),
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

fn migrate_deepseek_model(settings: &mut Settings, presence: SettingsPresence) -> bool {
    if presence.chat_model {
        return false;
    }
    let Some(model) = presence.deepseek_model else {
        return false;
    };
    settings.chat_model = model_selection_from_deepseek(model);
    true
}

fn migrate_deepseek_memory_model(
    settings: &mut Settings,
    presence: SettingsPresence,
    legacy_model: Option<DeepseekModel>,
) -> bool {
    if presence.memory_model {
        return false;
    }
    let model = presence.deepseek_memory_model.or(legacy_model);
    let Some(model) = model else {
        return false;
    };
    settings.memory_model = model_selection_from_deepseek(model);
    true
}

fn migrate_pet_avatar_size(settings: &mut Settings, presence: SettingsPresence) -> bool {
    if presence.pet_avatar_size {
        return false;
    }
    let Some(legacy_mode) = presence.legacy_pet_display_mode else {
        return false;
    };
    if legacy_mode == LegacyPetDisplayMode::EdgeRecall {
        settings.pet_avatar_size = PetAvatarSize::Mini;
    }
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

    let migrated_chat_model = migrate_deepseek_model(&mut loaded.settings, loaded.presence);
    let migrated_memory_model = migrate_deepseek_memory_model(
        &mut loaded.settings,
        loaded.presence,
        legacy_deepseek_memory_model(),
    );
    let migrated_pet_avatar_size = migrate_pet_avatar_size(&mut loaded.settings, loaded.presence);

    if (loaded.source == SettingsLoadSource::LegacyLocal
        || migrated_chat_model
        || migrated_memory_model
        || migrated_pet_avatar_size)
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
                if migrated_chat_model {
                    log::info!("settings: migrated legacy deepseekModel into chatModel");
                }
                if migrated_pet_avatar_size {
                    log::info!("settings: migrated legacy petDisplayMode into petAvatarSize");
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
        "settings: bootstrap resolved source={:?} path={:?} theme={} voiceTunnelConsentAccepted={} chatModel={:?} memoryModel={:?} avatarModelId={} petAvatarSize={}",
        loaded.source,
        loaded.path,
        loaded.settings.theme_attr(),
        loaded.settings.voice_tunnel_consent_accepted,
        loaded.settings.chat_model,
        loaded.settings.memory_model,
        loaded.settings.avatar_model_id,
        loaded.settings.pet_avatar_size.attr()
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
        "#,
        json = json,
    )
}

/// 启动期已加载的 settings 缓存在 state 中，供 command 快速读取。
fn current_state(state: tauri::State<'_, Arc<Mutex<Settings>>>) -> Result<Settings, String> {
    state
        .lock()
        .map(|s| s.clone())
        .map_err(|e| format!("settings state poisoned: {e}"))
}

pub(crate) fn migration_snapshot(state: &Arc<Mutex<Settings>>) -> Result<Settings, String> {
    state
        .lock()
        .map(|settings| settings.clone())
        .map_err(|error| format!("migration_settings_lock_failed: {error}"))
}

pub(crate) fn migration_replace<R: Runtime>(
    app: &AppHandle<R>,
    state: &Arc<Mutex<Settings>>,
    next: Settings,
) -> Result<Settings, String> {
    let previous = migration_snapshot(state)?;
    let store = app
        .store(paths::SETTINGS_STORE_PATH)
        .map_err(|error| format!("migration_settings_store_failed: {error}"))?;
    store.set(
        "settings",
        serde_json::to_value(&next)
            .map_err(|error| format!("migration_settings_serialize_failed: {error}"))?,
    );
    store
        .save()
        .map_err(|error| format!("migration_settings_save_failed: {error}"))?;
    *state
        .lock()
        .map_err(|error| format!("migration_settings_lock_failed: {error}"))? = next;
    request_power_policy_refresh(app);
    Ok(previous)
}

fn request_power_policy_refresh<R: Runtime>(app: &AppHandle<R>) {
    if let Some(manager) = app.try_state::<Arc<PowerPolicyManager>>() {
        manager.request_refresh();
    }
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
pub fn set_setting(
    app: AppHandle,
    state: tauri::State<'_, Arc<Mutex<Settings>>>,
    payload: SetPayload,
) -> Result<(), String> {
    let mut s = state
        .lock()
        .map_err(|e| format!("settings state poisoned: {e}"))?;

    let mut event_key = payload.key.clone();
    let mut event_value = payload.value.clone();

    match payload.key.as_str() {
        "theme" => {
            let theme: ThemeMode = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid theme: {e}"))?;
            s.theme = theme;
        }
        "backgroundOnlinePolicy" => {
            let policy: BackgroundOnlinePolicy = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid backgroundOnlinePolicy: {e}"))?;
            s.background_online_policy = policy;
        }
        "voiceTunnelConsentAccepted" => {
            let accepted: bool = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid voiceTunnelConsentAccepted: {e}"))?;
            s.voice_tunnel_consent_accepted = accepted;
        }
        "immersiveMode" => {
            let enabled: bool = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid immersiveMode: {e}"))?;
            s.immersive_mode = enabled;
        }
        "displayRecoveryProtectionEnabled" => {
            let enabled: bool = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid displayRecoveryProtectionEnabled: {e}"))?;
            s.display_recovery_protection_enabled = enabled;
        }
        "chatModel" => {
            let model: ModelSelection = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid chatModel: {e}"))?;
            s.chat_model = model;
        }
        "memoryModel" => {
            let model: ModelSelection = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid memoryModel: {e}"))?;
            s.memory_model = model;
        }
        "deepseekModel" => {
            let model: DeepseekModel = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid deepseekModel: {e}"))?;
            s.chat_model = model_selection_from_deepseek(model);
            event_key = "chatModel".to_string();
            event_value = serde_json::to_value(&s.chat_model).map_err(|e| e.to_string())?;
        }
        "deepseekMemoryModel" => {
            let model: DeepseekModel = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid deepseekMemoryModel: {e}"))?;
            s.memory_model = model_selection_from_deepseek(model);
            event_key = "memoryModel".to_string();
            event_value = serde_json::to_value(&s.memory_model).map_err(|e| e.to_string())?;
        }
        "avatarModelId" => {
            let model_id: String = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid avatarModelId: {e}"))?;
            s.avatar_model_id = model_id;
        }
        "petAvatarSize" => {
            let size: PetAvatarSize = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid petAvatarSize: {e}"))?;
            s.pet_avatar_size = size;
        }
        "avatarModelScalePercents" => {
            let scales: BTreeMap<String, u16> = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid avatarModelScalePercents: {e}"))?;
            for (model_id, value) in &scales {
                validate_percent(
                    *value,
                    50,
                    200,
                    &format!("avatarModelScalePercents.{model_id}"),
                )?;
            }
            s.avatar_model_scale_percents = scales;
        }
        "petMiniScalePercent" => {
            let percent: u16 = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid petMiniScalePercent: {e}"))?;
            s.pet_mini_scale_percent = validate_percent(percent, 25, 75, "petMiniScalePercent")?;
        }
        "petDisplayMode" => {
            let mode: LegacyPetDisplayMode = serde_json::from_value(payload.value.clone())
                .map_err(|e| format!("invalid legacy petDisplayMode: {e}"))?;
            s.pet_avatar_size = match mode {
                LegacyPetDisplayMode::Normal => PetAvatarSize::Normal,
                LegacyPetDisplayMode::EdgeRecall => PetAvatarSize::Mini,
            };
            event_key = "petAvatarSize".to_string();
            event_value = serde_json::to_value(&s.pet_avatar_size).map_err(|e| e.to_string())?;
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
        event_key,
        event_value
    );

    app.emit(
        EVENT_CHANGED,
        serde_json::json!({ "key": event_key, "value": event_value }),
    )
    .map_err(|e| e.to_string())?;

    if event_key == "displayRecoveryProtectionEnabled" && !s.display_recovery_protection_enabled {
        if let Err(error) = crate::pet_display_recovery::disable_protection(&app) {
            log::warn!("pet display recovery: failed to apply disabled setting: {error}");
        }
    }

    if event_key == "backgroundOnlinePolicy" {
        request_power_policy_refresh(&app);
    }

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
    fn product_defaults_use_kimi_2_5_for_chat_and_deepseek_flash_for_memory() {
        let settings = Settings::default();

        assert_eq!(settings.chat_model.provider, ModelProvider::Kimi);
        assert_eq!(settings.chat_model.model, "kimi/kimi-k2.5");
        assert_eq!(settings.memory_model.provider, ModelProvider::Deepseek);
        assert_eq!(settings.memory_model.model, "deepseek/deepseek-v4-flash");
    }

    #[test]
    fn default_background_online_policy_follows_system() {
        assert_eq!(
            Settings::default().background_online_policy,
            BackgroundOnlinePolicy::FollowSystem
        );
    }

    #[test]
    fn default_immersive_mode_is_enabled() {
        assert!(Settings::default().immersive_mode);
    }

    #[test]
    fn display_recovery_protection_defaults_to_disabled() {
        assert!(!Settings::default().display_recovery_protection_enabled);
    }

    #[test]
    fn missing_display_recovery_protection_in_legacy_settings_defaults_to_disabled() {
        let dir = temp_settings_dir("display-recovery-default");
        let path = dir.join("settings.json");
        write_raw(&path, r#"{"settings":{"theme":"light"}}"#);

        let settings = read_settings_file(&path).expect("read settings");

        assert!(!settings.display_recovery_protection_enabled);
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn missing_immersive_mode_in_legacy_settings_defaults_to_enabled() {
        let dir = temp_settings_dir("immersive-mode-default");
        let path = dir.join("settings.json");
        write_raw(&path, r#"{"settings":{"theme":"light"}}"#);

        let settings = read_settings_file(&path).expect("read settings");

        assert!(settings.immersive_mode);
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn explicit_transparent_mode_round_trips() {
        let dir = temp_settings_dir("immersive-mode-false");
        let path = dir.join("settings.json");
        write_raw(
            &path,
            r#"{"settings":{"theme":"light","immersiveMode":false}}"#,
        );

        let settings = read_settings_file(&path).expect("read settings");

        assert!(!settings.immersive_mode);
        assert!(serde_json::to_string(&settings)
            .expect("serialize settings")
            .contains(r#""immersiveMode":false"#));
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn default_avatar_model_id_uses_builtin_hiyori() {
        let settings = Settings::default();

        assert_eq!(settings.avatar_model_id, "builtin:live2d:hiyori");
    }

    #[test]
    fn default_pet_avatar_size_is_normal() {
        let settings = Settings::default();

        assert_eq!(settings.pet_avatar_size, PetAvatarSize::Normal);
    }

    #[test]
    fn default_avatar_scales_are_normal_and_half_size() {
        let settings = Settings::default();

        assert!(settings.avatar_model_scale_percents.is_empty());
        assert_eq!(settings.pet_mini_scale_percent, 50);
    }

    #[test]
    fn scale_percent_validation_uses_product_ranges_with_one_percent_steps() {
        assert_eq!(validate_percent(50, 50, 200, "model"), Ok(50));
        assert_eq!(validate_percent(51, 50, 200, "model"), Ok(51));
        assert_eq!(validate_percent(200, 50, 200, "model"), Ok(200));
        assert!(validate_percent(49, 50, 200, "model").is_err());
        assert_eq!(validate_percent(25, 25, 75, "mini"), Ok(25));
        assert_eq!(validate_percent(26, 25, 75, "mini"), Ok(26));
        assert!(validate_percent(80, 25, 75, "mini").is_err());
    }

    #[test]
    fn parse_settings_preserves_pet_avatar_size() {
        let dir = temp_settings_dir("pet-avatar-size");
        let path = dir.join("settings.json");
        write_raw(
            &path,
            r#"{
  "settings": {
    "theme": "light",
    "petAvatarSize": "mini"
  }
}"#,
        );

        let settings = read_settings_file(&path).expect("read settings");

        assert_eq!(settings.pet_avatar_size, PetAvatarSize::Mini);
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn load_from_paths_migrates_legacy_edge_recall_to_mini_avatar_size() {
        let dir = temp_settings_dir("pet-avatar-size-legacy-edge");
        let canonical = dir.join("settings.json");
        write_raw(
            &canonical,
            r#"{
  "settings": {
    "theme": "light",
    "petDisplayMode": "edgeRecall"
  }
}"#,
        );

        let loaded = load_from_paths(Some(canonical.clone()), None);

        assert_eq!(loaded.settings.pet_avatar_size, PetAvatarSize::Mini);
        let migrated = fs::read_to_string(&canonical).expect("read migrated");
        assert!(migrated.contains("\"petAvatarSize\": \"mini\""));
        assert!(!migrated.contains("petDisplayMode"));
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn migrate_deepseek_memory_model_fills_missing_legacy_value() {
        let mut settings = Settings::default();

        let changed = migrate_deepseek_memory_model(
            &mut settings,
            SettingsPresence {
                memory_model: false,
                ..SettingsPresence::default()
            },
            Some(DeepseekModel::Flash),
        );

        assert!(changed);
        assert_eq!(
            settings.memory_model,
            model_selection_from_deepseek(DeepseekModel::Flash)
        );
    }

    #[test]
    fn migrate_deepseek_memory_model_does_not_override_existing_setting() {
        let mut settings = Settings {
            memory_model: model_selection_from_deepseek(DeepseekModel::Pro),
            ..Settings::default()
        };

        let changed = migrate_deepseek_memory_model(
            &mut settings,
            SettingsPresence {
                memory_model: true,
                ..SettingsPresence::default()
            },
            Some(DeepseekModel::Flash),
        );

        assert!(!changed);
        assert_eq!(
            settings.memory_model,
            model_selection_from_deepseek(DeepseekModel::Pro)
        );
    }

    #[test]
    fn parse_settings_migrates_legacy_deepseek_models() {
        let dir = temp_settings_dir("legacy-deepseek-models");
        let canonical = dir.join("settings.json");
        write_raw(
            &canonical,
            r#"{
  "settings": {
    "theme": "light",
    "deepseekModel": "deepseek/deepseek-v4-pro",
    "deepseekMemoryModel": "deepseek/deepseek-v4-flash"
  }
}"#,
        );

        let loaded = load_from_paths(Some(canonical.clone()), None);

        assert_eq!(
            loaded.settings.chat_model,
            model_selection_from_deepseek(DeepseekModel::Pro)
        );
        assert_eq!(
            loaded.settings.memory_model,
            model_selection_from_deepseek(DeepseekModel::Flash)
        );
        let migrated = fs::read_to_string(&canonical).expect("read migrated");
        assert!(migrated.contains("\"chatModel\""));
        assert!(migrated.contains("\"memoryModel\""));
        assert!(!migrated.contains("deepseekModel"));
        assert!(!migrated.contains("deepseekMemoryModel"));
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn reads_shared_provider_aware_desktop_settings_fixture() {
        let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join("runtime_config")
            .join("tests")
            .join("fixtures")
            .join("desktop-settings-provider-aware.json");

        let settings = read_settings_file(&fixture).expect("read shared settings fixture");

        assert_eq!(settings.theme, ThemeMode::Dark);
        assert!(settings.voice_tunnel_consent_accepted);
        assert_eq!(settings.chat_model.provider, ModelProvider::Kimi);
        assert_eq!(settings.chat_model.model, "kimi/kimi-k2.7-code-highspeed");
        assert_eq!(settings.memory_model.provider, ModelProvider::Deepseek);
        assert_eq!(settings.memory_model.model, "deepseek/deepseek-v4-pro");
        assert_eq!(
            settings.avatar_model_id,
            "builtin:spine:ark-models:2025_shu"
        );
        assert_eq!(settings.pet_avatar_size, PetAvatarSize::Mini);
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
    fn build_init_script_injects_settings_snapshot() {
        let script = build_init_script(&Settings {
            theme: ThemeMode::Dark,
            ..Settings::default()
        });

        assert!(script.contains("window.__AGENT_FRIEND_SETTINGS__"));
        assert!(script.contains(r#""theme":"dark""#));
    }

    #[test]
    fn build_init_script_does_not_mutate_dom_theme() {
        let script = build_init_script(&Settings {
            theme: ThemeMode::Light,
            ..Settings::default()
        });

        assert!(!script.contains("document.documentElement.setAttribute('theme'"));
        assert!(!script.contains("theme-mode"));
    }

    #[test]
    fn build_init_script_injects_avatar_model_and_pet_avatar_size() {
        let script = build_init_script(&Settings {
            avatar_model_id: "builtin:spine:ark-models:103_angel".to_string(),
            pet_avatar_size: super::PetAvatarSize::Mini,
            ..Settings::default()
        });

        assert!(script.contains("\"avatarModelId\":\"builtin:spine:ark-models:103_angel\""));
        assert!(script.contains("\"petAvatarSize\":\"mini\""));
        assert!(!script.contains("petDisplayMode"));
    }
}
