//! 025 · Tauri 端日志根目录解析。
//!
//! 与 Python 侧 `agent.paths.log_dir()` 对齐：三端共享同一根目录，便于
//! 事后排查时一次定位 `agent_bridge.log` / `memory.log` / `tauri.log`。
//!
//! 优先级：``AGENT_FRIEND_LOG_DIR`` 环境变量 > 按平台手算。若某平台实测与
//! Python `platformdirs.user_log_dir` 偏差大，统一用 env 让两侧同步。

use std::path::PathBuf;

const LOG_DIR_ENV: &str = "AGENT_FRIEND_LOG_DIR";

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
