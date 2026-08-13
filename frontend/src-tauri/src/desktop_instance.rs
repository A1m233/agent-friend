use crate::{install_contract, paths};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

pub(crate) const PRODUCTION_INSTANCE_DOMAIN: &str = "agent-friend-production";
pub(crate) const INSTALLER_SMOKE_INSTANCE_DOMAIN: &str = "agent-friend-installer-smoke";
const OWNER_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum RuntimeKind {
    Production,
    Dev,
    InstallerSmoke,
}

impl RuntimeKind {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Production => "production",
            Self::Dev => "dev",
            Self::InstallerSmoke => "installer-smoke",
        }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct DesktopInstanceIdentity {
    pub(crate) runtime_kind: RuntimeKind,
    pub(crate) instance_domain: String,
    pub(crate) profile: install_contract::InstallProfile,
    pub(crate) build_identity: Option<install_contract::BuildIdentity>,
    pub(crate) executable_path: PathBuf,
    pub(crate) install_root: Option<PathBuf>,
    pub(crate) worktree: Option<PathBuf>,
}

pub(crate) struct DesktopInstanceState {
    pub(crate) identity: DesktopInstanceIdentity,
    pub(crate) owner: Arc<Mutex<Option<OwnerEvidence>>>,
}

impl DesktopInstanceState {
    pub(crate) fn new(
        identity: DesktopInstanceIdentity,
        owner: Arc<Mutex<Option<OwnerEvidence>>>,
    ) -> Self {
        Self { identity, owner }
    }

    pub(crate) fn snapshot(&self) -> Value {
        let owner = self
            .owner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .as_ref()
            .map(OwnerEvidence::safe_summary);
        json!({
            "schemaVersion": 1,
            "identity": self.identity.log_details("diagnostic-snapshot"),
            "owner": owner,
        })
    }
}

impl DesktopInstanceIdentity {
    pub(crate) fn resolve(
        resource_dir: Option<PathBuf>,
        runtime_tauri_identifier: &str,
    ) -> Result<Self, String> {
        let profile = install_contract::embedded_profile()?;
        let runtime_kind = if cfg!(debug_assertions) {
            RuntimeKind::Dev
        } else {
            match profile.profile_id.as_str() {
                "production" => RuntimeKind::Production,
                "installer-smoke" => RuntimeKind::InstallerSmoke,
                value => {
                    return Err(format!(
                        "identity_contract_invalid: unsupported release profile {value:?}"
                    ))
                }
            }
        };
        let expected_domain = expected_instance_domain(runtime_kind);
        if profile.schema_version != 1
            || profile.instance_domain != expected_domain
            || profile.bundle_identifier != expected_tauri_identifier(runtime_kind)
            || runtime_tauri_identifier != expected_tauri_identifier(runtime_kind)
            || (cfg!(debug_assertions) && profile.profile_id != "production")
        {
            return Err(format!(
                "identity_contract_invalid: profile {:?} does not match runtime kind {}",
                profile.profile_id,
                runtime_kind.as_str()
            ));
        }

        let build_identity = install_contract::embedded_identity()?.map(|(identity, _)| identity);
        if let Some(identity) = build_identity.as_ref() {
            if identity.profile_id != profile.profile_id {
                return Err(
                    "identity_contract_invalid: build identity profile does not match install profile"
                        .to_string(),
                );
            }
        } else if !cfg!(debug_assertions) {
            return Err("identity_contract_invalid: release build identity is missing".to_string());
        }

        let executable_path = std::env::current_exe()
            .map_err(|error| format!("identity_contract_invalid: current executable: {error}"))?;
        let worktree = if runtime_kind == RuntimeKind::Dev {
            std::env::var_os("AGENT_FRIEND_REPO_ROOT").map(PathBuf::from)
        } else {
            None
        };
        let install_root = if runtime_kind == RuntimeKind::Dev {
            resource_dir
        } else {
            executable_path.parent().map(Path::to_path_buf)
        };

        Ok(Self {
            runtime_kind,
            instance_domain: expected_domain.to_string(),
            profile,
            build_identity,
            executable_path,
            install_root,
            worktree,
        })
    }

    pub(crate) fn log_details(&self, attempt_id: &str) -> Value {
        json!({
            "attemptId": attempt_id,
            "runtimeKind": self.runtime_kind,
            "instanceDomain": self.instance_domain,
            "profileId": self.profile.profile_id,
            "tauriIdentifier": self.profile.bundle_identifier,
            "buildIdentity": self.build_identity,
            "executablePath": redact_home(&self.executable_path),
            "installRoot": self.install_root.as_deref().map(redact_home),
            "worktree": self.worktree.as_deref().map(redact_home),
        })
    }
}

pub(crate) fn expected_instance_domain(runtime_kind: RuntimeKind) -> &'static str {
    match runtime_kind {
        RuntimeKind::Production | RuntimeKind::Dev => PRODUCTION_INSTANCE_DOMAIN,
        RuntimeKind::InstallerSmoke => INSTALLER_SMOKE_INSTANCE_DOMAIN,
    }
}

fn expected_tauri_identifier(runtime_kind: RuntimeKind) -> &'static str {
    match runtime_kind {
        RuntimeKind::Production | RuntimeKind::Dev => "com.agentfriend.desktop",
        RuntimeKind::InstallerSmoke => "com.agentfriend.desktop.installer-smoke",
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct OwnerEvidence {
    schema_version: u32,
    pub(crate) instance_domain: String,
    pub(crate) runtime_kind: RuntimeKind,
    pub(crate) pid: u32,
    process_start_identity: String,
    pub(crate) process_session_id: String,
    pub(crate) profile_id: String,
    tauri_identifier: String,
    build_identity: Option<install_contract::BuildIdentity>,
    executable_path: PathBuf,
    install_root: Option<PathBuf>,
    worktree: Option<PathBuf>,
    published_at: String,
}

impl OwnerEvidence {
    pub(crate) fn for_current_process(
        identity: &DesktopInstanceIdentity,
        process_session_id: &str,
    ) -> Result<Self, String> {
        let process_start_identity = match probe_process(std::process::id()) {
            ProcessProbe::Running(value) => value,
            ProcessProbe::Missing => {
                return Err("owner_process_mismatch: current process is unavailable".to_string())
            }
            ProcessProbe::Unknown(error) => {
                return Err(format!(
                    "owner_process_mismatch: current process start identity: {error}"
                ))
            }
        };
        Ok(Self {
            schema_version: OWNER_SCHEMA_VERSION,
            instance_domain: identity.instance_domain.clone(),
            runtime_kind: identity.runtime_kind,
            pid: std::process::id(),
            process_start_identity,
            process_session_id: process_session_id.to_string(),
            profile_id: identity.profile.profile_id.clone(),
            tauri_identifier: identity.profile.bundle_identifier.clone(),
            build_identity: identity.build_identity.clone(),
            executable_path: identity.executable_path.clone(),
            install_root: identity.install_root.clone(),
            worktree: identity.worktree.clone(),
            published_at: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        })
    }

    pub(crate) fn safe_summary(&self) -> Value {
        json!({
            "instanceDomain": self.instance_domain,
            "runtimeKind": self.runtime_kind,
            "pid": self.pid,
            "processSessionId": self.process_session_id,
            "profileId": self.profile_id,
            "tauriIdentifier": self.tauri_identifier,
            "buildIdentity": self.build_identity,
            "executablePath": redact_home(&self.executable_path),
            "installRoot": self.install_root.as_deref().map(redact_home),
            "worktree": self.worktree.as_deref().map(redact_home),
            "publishedAt": self.published_at,
        })
    }
}

#[derive(Debug)]
pub(crate) enum OwnerInspection {
    Missing,
    Live(OwnerEvidence),
    Stale {
        previous: OwnerEvidence,
        reason: String,
    },
    Invalid(String),
}

pub(crate) fn inspect_owner(identity: &DesktopInstanceIdentity) -> OwnerInspection {
    inspect_owner_path(&owner_path(&identity.instance_domain), identity)
}

fn inspect_owner_path(path: &Path, identity: &DesktopInstanceIdentity) -> OwnerInspection {
    let bytes = match fs::read(path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => return OwnerInspection::Missing,
        Err(error) => {
            return OwnerInspection::Invalid(format!(
                "owner_evidence_invalid: read {}: {error}",
                path.display()
            ))
        }
    };
    let owner: OwnerEvidence = match serde_json::from_slice(&bytes) {
        Ok(value) => value,
        Err(error) => {
            return OwnerInspection::Invalid(format!(
                "owner_evidence_invalid: parse {}: {error}",
                path.display()
            ))
        }
    };
    if owner.schema_version != OWNER_SCHEMA_VERSION
        || owner.instance_domain != identity.instance_domain
        || owner.tauri_identifier != identity.profile.bundle_identifier
    {
        return OwnerInspection::Invalid(
            "owner_evidence_invalid: owner contract does not match instance domain".to_string(),
        );
    }
    match probe_process(owner.pid) {
        ProcessProbe::Running(actual) if actual == owner.process_start_identity => {
            OwnerInspection::Live(owner)
        }
        ProcessProbe::Running(_) => OwnerInspection::Stale {
            previous: owner,
            reason: "owner-pid-reused".to_string(),
        },
        ProcessProbe::Missing => OwnerInspection::Stale {
            previous: owner,
            reason: "owner-process-exited".to_string(),
        },
        ProcessProbe::Unknown(error) => OwnerInspection::Invalid(format!(
            "owner_process_mismatch: could not validate owner process: {error}"
        )),
    }
}

pub(crate) fn publish_owner(owner: &OwnerEvidence, attempt_id: &str) -> Result<(), String> {
    let directory = paths::instance_coordination_dir();
    fs::create_dir_all(&directory)
        .map_err(|error| format!("owner_evidence_invalid: create directory: {error}"))?;
    let target = owner_path(&owner.instance_domain);
    let temporary = directory.join(format!(
        ".{}.{}.tmp",
        owner.instance_domain,
        sanitize_component(attempt_id)
    ));
    let bytes = serde_json::to_vec_pretty(owner)
        .map_err(|error| format!("owner_evidence_invalid: serialize owner: {error}"))?;
    fs::write(&temporary, bytes)
        .map_err(|error| format!("owner_evidence_invalid: write owner: {error}"))?;
    if let Err(error) = fs::rename(&temporary, &target) {
        #[cfg(target_os = "windows")]
        if target.exists() {
            fs::remove_file(&target).map_err(|remove_error| {
                format!(
                    "owner_evidence_invalid: replace owner after {error}; remove target: {remove_error}"
                )
            })?;
            fs::rename(&temporary, &target).map_err(|rename_error| {
                format!("owner_evidence_invalid: replace owner: {rename_error}")
            })?;
        } else {
            return Err(format!("owner_evidence_invalid: publish owner: {error}"));
        }
        #[cfg(not(target_os = "windows"))]
        return Err(format!("owner_evidence_invalid: publish owner: {error}"));
    }
    Ok(())
}

pub(crate) fn clear_owner_if_current(owner: &OwnerEvidence) {
    let path = owner_path(&owner.instance_domain);
    let Ok(bytes) = fs::read(&path) else {
        return;
    };
    let Ok(current) = serde_json::from_slice::<OwnerEvidence>(&bytes) else {
        return;
    };
    if current.pid == owner.pid
        && current.process_start_identity == owner.process_start_identity
        && current.process_session_id == owner.process_session_id
    {
        let _ = fs::remove_file(path);
    }
}

pub(crate) fn report_startup_rejection(message: &str) {
    eprintln!("agent-friend startup rejected: {message}");
    let _ = std::panic::catch_unwind(|| {
        rfd::MessageDialog::new()
            .set_title("agent-friend 无法启动")
            .set_description(message)
            .set_level(rfd::MessageLevel::Warning)
            .set_buttons(rfd::MessageButtons::Ok)
            .show()
    });
}

pub(crate) fn owner_path(instance_domain: &str) -> PathBuf {
    paths::instance_coordination_dir().join(format!("{instance_domain}.owner.json"))
}

fn sanitize_component(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '-' {
                character
            } else {
                '_'
            }
        })
        .collect()
}

fn redact_home(path: &Path) -> String {
    let rendered = path.to_string_lossy();
    let Some(home) = dirs::home_dir() else {
        return rendered.into_owned();
    };
    let home = home.to_string_lossy();
    if rendered.starts_with(home.as_ref()) {
        format!("<home>{}", &rendered[home.len()..])
    } else {
        rendered.into_owned()
    }
}

enum ProcessProbe {
    Running(String),
    Missing,
    Unknown(String),
}

#[cfg(target_os = "linux")]
fn probe_process(pid: u32) -> ProcessProbe {
    let path = PathBuf::from(format!("/proc/{pid}/stat"));
    let content = match fs::read_to_string(path) {
        Ok(value) => value,
        Err(error) if error.kind() == ErrorKind::NotFound => return ProcessProbe::Missing,
        Err(error) => return ProcessProbe::Unknown(error.to_string()),
    };
    let Some((_, fields)) = content.rsplit_once(')') else {
        return ProcessProbe::Unknown("invalid /proc stat".to_string());
    };
    let fields = fields.split_whitespace().collect::<Vec<_>>();
    match fields.get(19) {
        Some(value) => ProcessProbe::Running((*value).to_string()),
        None => ProcessProbe::Unknown("missing /proc process start field".to_string()),
    }
}

#[cfg(target_os = "macos")]
fn probe_process(pid: u32) -> ProcessProbe {
    let output = match std::process::Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "lstart="])
        .output()
    {
        Ok(value) => value,
        Err(error) => return ProcessProbe::Unknown(error.to_string()),
    };
    if !output.status.success() {
        return ProcessProbe::Missing;
    }
    let value = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if value.is_empty() {
        ProcessProbe::Missing
    } else {
        ProcessProbe::Running(value)
    }
}

#[cfg(target_os = "windows")]
fn probe_process(pid: u32) -> ProcessProbe {
    use windows::Win32::{
        Foundation::{CloseHandle, FILETIME},
        System::Threading::{GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    };

    let handle = match unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) } {
        Ok(value) => value,
        Err(error) if error.code().0 == unchecked_hresult(0x80070057) => {
            return ProcessProbe::Missing
        }
        Err(error) => return ProcessProbe::Unknown(error.to_string()),
    };
    let mut creation = FILETIME::default();
    let mut exit = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    let result =
        unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) };
    unsafe {
        let _ = CloseHandle(handle);
    }
    match result {
        Ok(()) => ProcessProbe::Running(format!(
            "{:08x}{:08x}",
            creation.dwHighDateTime, creation.dwLowDateTime
        )),
        Err(error) => ProcessProbe::Unknown(error.to_string()),
    }
}

#[cfg(target_os = "windows")]
const fn unchecked_hresult(value: u32) -> i32 {
    value as i32
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity(runtime_kind: RuntimeKind) -> DesktopInstanceIdentity {
        let profile = match runtime_kind {
            RuntimeKind::InstallerSmoke => install_contract::InstallProfile {
                schema_version: 1,
                profile_id: "installer-smoke".to_string(),
                instance_domain: INSTALLER_SMOKE_INSTANCE_DOMAIN.to_string(),
                product_name: "agent-friend-installer-smoke".to_string(),
                bundle_identifier: "com.agentfriend.desktop.installer-smoke".to_string(),
                main_binary_name: "agent-friend-installer-smoke".to_string(),
                install_dir_name: "agent-friend-installer-smoke".to_string(),
                registry_product_name: "agent-friend-installer-smoke".to_string(),
                autostart_value_name: "agent-friend-installer-smoke".to_string(),
                data_namespace: "agent-friend-installer-smoke".to_string(),
                settings_namespace: "com.agentfriend.desktop.installer-smoke".to_string(),
                log_namespace: "agent-friend-installer-smoke".to_string(),
                keyring_service: "agent-friend-installer-smoke".to_string(),
                bridge_port: 28_800,
                voice_port: 28_900,
            },
            _ => install_contract::production_profile(),
        };
        DesktopInstanceIdentity {
            runtime_kind,
            instance_domain: expected_instance_domain(runtime_kind).to_string(),
            profile,
            build_identity: None,
            executable_path: PathBuf::from("agent-friend-test"),
            install_root: None,
            worktree: None,
        }
    }

    #[test]
    fn production_and_dev_share_a_domain_but_smoke_is_isolated() {
        assert_eq!(
            expected_instance_domain(RuntimeKind::Production),
            expected_instance_domain(RuntimeKind::Dev)
        );
        assert_ne!(
            expected_instance_domain(RuntimeKind::Production),
            expected_instance_domain(RuntimeKind::InstallerSmoke)
        );
    }

    #[test]
    fn runtime_tauri_identifier_mismatch_fails_closed() {
        let error = DesktopInstanceIdentity::resolve(None, "com.example.wrong")
            .expect_err("mismatched Tauri identifier must fail");
        assert!(error.contains("identity_contract_invalid"));
    }

    #[test]
    fn current_owner_round_trips_and_is_live() {
        let identity = identity(RuntimeKind::Dev);
        let owner = OwnerEvidence::for_current_process(&identity, "test-session")
            .expect("create current owner");
        let root = std::env::temp_dir().join(format!(
            "agent-friend-owner-test-{}-{}",
            std::process::id(),
            rand::random::<u64>()
        ));
        fs::create_dir_all(&root).expect("create temp root");
        let path = root.join("owner.json");
        fs::write(&path, serde_json::to_vec(&owner).unwrap()).expect("write owner");
        assert!(matches!(
            inspect_owner_path(&path, &identity),
            OwnerInspection::Live(_)
        ));
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn malformed_owner_fails_closed() {
        let identity = identity(RuntimeKind::Dev);
        let root = std::env::temp_dir().join(format!(
            "agent-friend-owner-invalid-test-{}-{}",
            std::process::id(),
            rand::random::<u64>()
        ));
        fs::create_dir_all(&root).expect("create temp root");
        let path = root.join("owner.json");
        fs::write(&path, b"not-json").expect("write owner");
        assert!(matches!(
            inspect_owner_path(&path, &identity),
            OwnerInspection::Invalid(_)
        ));
        fs::remove_dir_all(root).ok();
    }
}
