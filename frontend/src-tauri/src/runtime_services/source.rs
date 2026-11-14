//! Launch policy for services run from a development checkout.
use super::model::RuntimeServiceKind;
use std::{
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};

pub(super) const READINESS_TIMEOUT: Duration = Duration::from_secs(60);

pub(super) fn command(root: &Path, service: RuntimeServiceKind) -> Result<Command, String> {
    let uv_executable = resolve_path_executable("uv").ok_or_else(|| {
        "managed_execution_environment_unavailable: uv is not available for source runtime"
            .to_string()
    })?;
    let mut command = Command::new(&uv_executable);
    command
        .args(["run", "python", "-m", service.module()])
        .current_dir(root)
        .env("AGENT_BRIDGE_DEV_MODE", "true")
        .env("AGENT_FRIEND_UV_EXECUTABLE", uv_executable)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    Ok(command)
}

fn resolve_path_executable(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    #[cfg(windows)]
    let suffixes = ["", ".exe", ".cmd", ".bat"];
    #[cfg(not(windows))]
    let suffixes = [""];
    for directory in std::env::split_paths(&path) {
        for suffix in suffixes {
            let candidate = directory.join(format!("{name}{suffix}"));
            if candidate.is_file() {
                return candidate.canonicalize().ok().or(Some(candidate));
            }
        }
    }
    None
}
