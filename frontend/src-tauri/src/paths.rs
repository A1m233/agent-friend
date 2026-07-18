//! Tauri shell 的落盘路径统一入口。
//!
//! Python/bridge 侧用户数据路径仍由 `agent.paths` 负责；这里只管理 Tauri 壳自身
//! 需要知道的路径，例如 plugin-store settings 文件与 Tauri 日志目录。

use std::path::PathBuf;

pub const BUNDLE_ID: &str = "com.agentfriend.desktop";
pub const APP_NAME: &str = "agent-friend";
pub const SETTINGS_STORE_PATH: &str = "settings.json";
pub const UI_STATE_STORE_PATH: &str = "ui-state.json";
pub const PERMISSION_STATE_FILE: &str = "permission-state.json";
pub const AVATAR_MODELS_DIR: &str = "avatar-models";
pub const AVATAR_MODEL_CATALOG_FILE: &str = "catalog.json";
pub const PERSONA_EDITABLE_DIR: &str = "personas/default";
pub const PERSONA_EDITABLE_FILE: &str = "persona.md";
pub const MIGRATION_DIR: &str = "migration";
pub const MIGRATION_BACKUPS_DIR: &str = "migration-backups";

const DATA_DIR_ENV: &str = "AGENT_FRIEND_DATA_DIR";
const LOG_DIR_ENV: &str = "AGENT_FRIEND_LOG_DIR";

fn app_store_path(base: PathBuf) -> PathBuf {
    base.join(BUNDLE_ID).join(SETTINGS_STORE_PATH)
}

/// Tauri plugin-store 的 settings 文件绝对路径。
///
/// 本项目显式按 `dirs::config_dir() / BUNDLE_ID / settings.json` 解析，
/// 与 Windows 上 `tauri-plugin-store` 实际写入的 Roaming 路径保持一致，避免启动期同步读盘依赖
/// AppHandle 才能拿到 path resolver。
pub fn settings_store_path() -> Option<PathBuf> {
    dirs::config_dir().map(app_store_path)
}

/// 统一系统权限模块的处理历史；只保存应用历史，不保存系统状态真相。
pub fn permission_state_path() -> Option<PathBuf> {
    dirs::config_dir().map(|base| base.join(BUNDLE_ID).join(PERMISSION_STATE_FILE))
}

/// 历史版本曾误按 local data dir 读 settings；启动期只作为兼容 fallback 使用。
pub fn legacy_local_settings_store_path() -> Option<PathBuf> {
    dirs::data_local_dir().map(app_store_path)
}

/// Tauri 壳可访问的用户数据根目录。
///
/// 与 Python 侧 `agent.paths.user_data_dir()` 对齐：用户资产走 `agent-friend`
/// 应用数据目录，settings 仍走 `com.agentfriend.desktop/settings.json`。
/// 优先级：`AGENT_FRIEND_DATA_DIR` > 平台默认用户数据目录。
pub fn user_data_dir() -> PathBuf {
    if let Ok(p) = std::env::var(DATA_DIR_ENV) {
        return PathBuf::from(p);
    }

    dirs::data_dir().expect("data dir").join(APP_NAME)
}

/// Avatar 模型资源根目录：`<user_data_dir>/avatar-models`。
pub fn avatar_model_assets_dir() -> PathBuf {
    user_data_dir().join(AVATAR_MODELS_DIR)
}

/// 应用随包内置模型安装后的管理目录：`<avatar_model_assets_dir>/builtin`。
pub fn builtin_avatar_models_dir() -> PathBuf {
    avatar_model_assets_dir().join("builtin")
}

/// 用户导入模型 catalog 预留位置：`<avatar_model_assets_dir>/catalog.json`。
pub fn avatar_model_catalog_path() -> PathBuf {
    avatar_model_assets_dir().join(AVATAR_MODEL_CATALOG_FILE)
}

/// 050：唯一公开可编辑的人格目录。
pub fn editable_persona_dir() -> PathBuf {
    user_data_dir().join(PERSONA_EDITABLE_DIR)
}

/// 050：唯一公开可编辑的人格正文。
pub fn editable_persona_path() -> PathBuf {
    editable_persona_dir().join(PERSONA_EDITABLE_FILE)
}

/// 迁移事务工作区；不属于被替换资产，启动恢复依赖此目录。
pub fn migration_work_dir() -> PathBuf {
    user_data_dir().join(MIGRATION_DIR)
}

/// 当前设备唯一一份最近有效回滚备份所在目录。
pub fn migration_backups_dir() -> PathBuf {
    user_data_dir().join(MIGRATION_BACKUPS_DIR)
}

/// Tauri / bridge / memory 共享的日志目录。
///
/// 与 Python 侧 `agent.paths.log_dir()` 对齐：三端共享同一根目录，便于事后排查。
/// 优先级：`AGENT_FRIEND_LOG_DIR` > 按平台手算。
pub fn log_dir() -> PathBuf {
    if let Ok(p) = std::env::var(LOG_DIR_ENV) {
        return PathBuf::from(p);
    }

    #[cfg(target_os = "macos")]
    {
        dirs::home_dir()
            .expect("home dir")
            .join("Library/Logs/agent-friend")
    }

    #[cfg(target_os = "windows")]
    {
        dirs::data_local_dir()
            .expect("local data dir")
            .join("agent-friend")
            .join("Logs")
    }

    #[cfg(target_os = "linux")]
    {
        if let Ok(state) = std::env::var("XDG_STATE_HOME") {
            PathBuf::from(state).join("agent-friend").join("log")
        } else {
            dirs::home_dir()
                .expect("home dir")
                .join(".local/state/agent-friend/log")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn app_store_path_uses_bundle_and_store_file() {
        let path = app_store_path(PathBuf::from("base"));
        assert_eq!(
            path,
            PathBuf::from("base")
                .join(BUNDLE_ID)
                .join(SETTINGS_STORE_PATH)
        );
    }

    #[test]
    fn permission_history_uses_the_app_config_directory() {
        let path = permission_state_path().expect("permission state path");
        assert_eq!(path.file_name().unwrap(), PERMISSION_STATE_FILE);
        assert_eq!(path.parent().unwrap().file_name().unwrap(), BUNDLE_ID);
    }

    #[test]
    fn avatar_model_paths_use_user_data_dir_override() {
        let _guard = ENV_LOCK.lock().expect("env lock");
        let previous = std::env::var_os(DATA_DIR_ENV);
        std::env::set_var(DATA_DIR_ENV, "agent-friend-data");

        assert_eq!(user_data_dir(), PathBuf::from("agent-friend-data"));
        assert_eq!(
            avatar_model_assets_dir(),
            PathBuf::from("agent-friend-data").join(AVATAR_MODELS_DIR)
        );
        assert_eq!(
            avatar_model_catalog_path(),
            PathBuf::from("agent-friend-data")
                .join(AVATAR_MODELS_DIR)
                .join(AVATAR_MODEL_CATALOG_FILE)
        );
        assert_eq!(
            builtin_avatar_models_dir(),
            PathBuf::from("agent-friend-data")
                .join(AVATAR_MODELS_DIR)
                .join("builtin")
        );
        assert_eq!(
            editable_persona_path(),
            PathBuf::from("agent-friend-data")
                .join("personas/default")
                .join("persona.md")
        );

        if let Some(value) = previous {
            std::env::set_var(DATA_DIR_ENV, value);
        } else {
            std::env::remove_var(DATA_DIR_ENV);
        }
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn settings_store_path_uses_roaming_appdata_on_windows() {
        let path = settings_store_path().expect("settings path");
        let appdata = std::env::var("APPDATA").expect("APPDATA");
        let local_appdata = std::env::var("LOCALAPPDATA").expect("LOCALAPPDATA");

        assert!(path.starts_with(appdata));
        assert!(!path.starts_with(local_appdata));
    }
}
