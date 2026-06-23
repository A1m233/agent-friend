use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};
use tauri::{
    plugin::{Builder as PluginBuilder, TauriPlugin},
    AppHandle, Emitter, Manager, Runtime,
};
use tauri_plugin_store::StoreExt;

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub theme: ThemeMode,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "lowercase")]
pub enum ThemeMode {
    Light,
    Dark,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            theme: ThemeMode::Light,
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

const BUNDLE_ID: &str = "com.agentfriend.desktop";
const STORE_PATH: &str = "settings.json";
const EVENT_CHANGED: &str = "settings://changed";

/// 028 · 用 OS 本地数据目录 + bundle id 拼出 tauri-plugin-store 默认文件路径。
///
/// `tauri-plugin-store` 默认把 store 文件放在 `app_local_data_dir()` 下，即
/// `dirs::data_local_dir() / bundle_id / STORE_PATH`（macOS 上对应
/// `~/Library/Application Support/com.agentfriend.desktop/settings.json`）。
/// 启动期（plugin builder 阶段）同步读盘，所有错误路径返回默认值，不阻断启动。
pub fn load_from_disk_or_default() -> Settings {
    let Some(data_dir) = dirs::data_local_dir() else {
        log::warn!("settings: cannot resolve data_local_dir");
        return Settings::default();
    };
    let path = data_dir.join(BUNDLE_ID).join(STORE_PATH);
    log::info!("settings: bootstrap loading from {:?}", path);
    let settings = match std::fs::read_to_string(&path) {
        Ok(raw) => {
            // `tauri-plugin-store` 把值存在顶层 key "settings" 下，因此启动期读盘时
            // 要先取这个 wrapper，再反序列化为 `Settings`。
            serde_json::from_str::<serde_json::Value>(&raw)
                .and_then(|v| match v.get("settings") {
                    Some(inner) => serde_json::from_value::<Settings>(inner.clone()),
                    None => serde_json::from_value::<Settings>(v),
                })
                .unwrap_or_else(|e| {
                    log::warn!(
                        "settings: parse {:?} failed: {e}, using default",
                        path
                    );
                    Settings::default()
                })
        }
        Err(e) => {
            log::info!("settings: read {:?} failed: {e}, using default", path);
            Settings::default()
        }
    };
    log::info!("settings: bootstrap resolved theme={}", settings.theme_attr());
    settings
}

/// 把当前 settings 拼成 webview 初始化脚本。会在每个 webview 加载 HTML 之前同步执行，
/// 保证首帧主题正确、window.__AGENT_FRIEND_SETTINGS__ 立即可用。
pub fn build_init_script(s: &Settings) -> String {
    let json = serde_json::to_string(s).expect("settings serialize");
    format!(
        r#"
        window.__AGENT_FRIEND_SETTINGS__ = {json};
        document.documentElement.setAttribute('theme', '{theme}');
        "#,
        json = json,
        theme = s.theme_attr(),
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
            let theme: ThemeMode =
                serde_json::from_value(payload.value.clone())
                    .map_err(|e| format!("invalid theme: {e}"))?;
            s.theme = theme;
        }
        _ => return Err(format!("unknown setting key: {}", payload.key)),
    }

    let store = app.store(STORE_PATH).map_err(|e| e.to_string())?;
    store.set(
        "settings",
        serde_json::to_value(&*s).map_err(|e| e.to_string())?,
    );
    store.save().map_err(|e| e.to_string())?;

    // 028 · R3：把 plugin-store 实际用的 app_config_dir 打到日志，与 bootstrap 读路径对比。
    if let Ok(config_dir) = app.path().app_config_dir() {
        let store_path: std::path::PathBuf = config_dir.join(STORE_PATH);
        log::info!(
            "settings: store saved to {:?} (bootstrap read from dirs::config_dir + bundle id)",
            store_path
        );
    }

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

/// 028 · settings facade plugin。
/// js_init_script 在 lib.rs 构造 plugin 时由外部传入（启动期同步读盘后拼接）。
/// command 直接注册到 app 级 invoke_handler，避免自定义 plugin 权限文件开销。
pub fn init<R: Runtime>(js_init_script: String) -> TauriPlugin<R> {
    PluginBuilder::new("agent-friend-settings")
        .js_init_script(js_init_script)
        .build()
}
