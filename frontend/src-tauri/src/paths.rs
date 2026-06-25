//! Tauri shell 的落盘路径统一入口。
//!
//! Python/bridge 侧用户数据路径仍由 `agent.paths` 负责；这里只管理 Tauri 壳自身
//! 需要知道的路径，例如 plugin-store settings 文件与 Tauri 日志目录。

use std::path::PathBuf;

pub const BUNDLE_ID: &str = "com.agentfriend.desktop";
pub const SETTINGS_STORE_PATH: &str = "settings.json";

const LOG_DIR_ENV: &str = "AGENT_FRIEND_LOG_DIR";

/// Tauri plugin-store 的 settings 文件绝对路径。
///
/// 本项目显式按 `dirs::data_local_dir() / BUNDLE_ID / settings.json` 解析，
/// 与既有 `tauri-plugin-store` 写盘位置保持一致，避免启动期同步读盘依赖
/// AppHandle 才能拿到 path resolver。
pub fn settings_store_path() -> Option<PathBuf> {
    dirs::data_local_dir().map(|dir| dir.join(BUNDLE_ID).join(SETTINGS_STORE_PATH))
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
