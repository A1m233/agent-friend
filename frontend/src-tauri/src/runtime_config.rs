use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

const KEYRING_SERVICE: &str = "agent-friend";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeConfigKind {
    Secret,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeConfigGroup {
    Amap,
    Deepseek,
    Kimi,
    Tavily,
    VolcVoice,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum RuntimeConfigSource {
    #[serde(rename = "env")]
    Env,
    #[serde(rename = "keyring")]
    Keyring,
    #[serde(rename = "legacy-dotenv")]
    LegacyDotenv,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeConfigEffect {
    RestartBridge,
    RestartVoice,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConfigItemStatus {
    pub name: String,
    pub kind: RuntimeConfigKind,
    pub group: RuntimeConfigGroup,
    pub configured: bool,
    pub source: Option<RuntimeConfigSource>,
    pub effect: RuntimeConfigEffect,
    pub error: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConfigSnapshot {
    pub items: Vec<RuntimeConfigItemStatus>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeConfigMutation {
    pub item: RuntimeConfigItemStatus,
    pub effect: RuntimeConfigEffect,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetRuntimeSecretPayload {
    pub name: String,
    pub value: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DeleteRuntimeSecretPayload {
    pub name: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct SecretSpec {
    name: &'static str,
    aliases: &'static [&'static str],
    group: RuntimeConfigGroup,
    effect: RuntimeConfigEffect,
}

const SECRET_REGISTRY: &[SecretSpec] = &[
    SecretSpec {
        name: "AMAP_WEBSERVICE_KEY",
        aliases: &[],
        group: RuntimeConfigGroup::Amap,
        effect: RuntimeConfigEffect::RestartBridge,
    },
    SecretSpec {
        name: "DEEPSEEK_API_KEY",
        aliases: &[],
        group: RuntimeConfigGroup::Deepseek,
        effect: RuntimeConfigEffect::RestartBridge,
    },
    SecretSpec {
        name: "KIMI_API_KEY",
        aliases: &[],
        group: RuntimeConfigGroup::Kimi,
        effect: RuntimeConfigEffect::RestartBridge,
    },
    SecretSpec {
        name: "TAVILY_API_KEY",
        aliases: &[],
        group: RuntimeConfigGroup::Tavily,
        effect: RuntimeConfigEffect::RestartBridge,
    },
    SecretSpec {
        name: "VOLC_ACCESS_KEY",
        aliases: &["VOICE_BRIDGE_VOLC_ACCESS_KEY"],
        group: RuntimeConfigGroup::VolcVoice,
        effect: RuntimeConfigEffect::RestartVoice,
    },
    SecretSpec {
        name: "VOLC_SECRET_KEY",
        aliases: &["VOICE_BRIDGE_VOLC_SECRET_KEY"],
        group: RuntimeConfigGroup::VolcVoice,
        effect: RuntimeConfigEffect::RestartVoice,
    },
    SecretSpec {
        name: "VOLC_RTC_APP_ID",
        aliases: &["VOICE_BRIDGE_VOLC_RTC_APP_ID"],
        group: RuntimeConfigGroup::VolcVoice,
        effect: RuntimeConfigEffect::RestartVoice,
    },
    SecretSpec {
        name: "VOLC_RTC_APP_KEY",
        aliases: &["VOICE_BRIDGE_VOLC_RTC_APP_KEY"],
        group: RuntimeConfigGroup::VolcVoice,
        effect: RuntimeConfigEffect::RestartVoice,
    },
    SecretSpec {
        name: "VOLC_SPEECH_APP_ID",
        aliases: &["VOICE_BRIDGE_VOLC_SPEECH_APP_ID"],
        group: RuntimeConfigGroup::VolcVoice,
        effect: RuntimeConfigEffect::RestartVoice,
    },
    SecretSpec {
        name: "VOLC_SPEECH_ACCESS_TOKEN",
        aliases: &["VOICE_BRIDGE_VOLC_SPEECH_ACCESS_TOKEN"],
        group: RuntimeConfigGroup::VolcVoice,
        effect: RuntimeConfigEffect::RestartVoice,
    },
    SecretSpec {
        name: "VOLC_SPEECH_API_KEY",
        aliases: &["VOICE_BRIDGE_VOLC_SPEECH_API_KEY"],
        group: RuntimeConfigGroup::VolcVoice,
        effect: RuntimeConfigEffect::RestartVoice,
    },
];

fn find_secret(name: &str) -> Option<&'static SecretSpec> {
    SECRET_REGISTRY.iter().find(|spec| spec.name == name)
}

fn require_secret(name: &str) -> Result<&'static SecretSpec, String> {
    find_secret(name).ok_or_else(|| format!("unknown runtime secret: {name}"))
}

fn python_keyring_compound_target(name: &str) -> String {
    format!("{name}@{KEYRING_SERVICE}")
}

fn python_keyring_service_target() -> &'static str {
    KEYRING_SERVICE
}

#[cfg(test)]
fn rust_keyring_default_target(name: &str) -> String {
    format!("{name}.{KEYRING_SERVICE}")
}

#[cfg(target_os = "windows")]
fn keyring_entry(name: &str) -> Result<keyring::Entry, String> {
    let target = python_keyring_compound_target(name);
    keyring::Entry::new_with_target(&target, KEYRING_SERVICE, name)
        .map_err(|e| format!("runtime config keyring entry failed for {name}: {e}"))
}

#[cfg(not(target_os = "windows"))]
fn keyring_entry(name: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, name)
        .map_err(|e| format!("runtime config keyring entry failed for {name}: {e}"))
}

#[cfg(target_os = "windows")]
fn legacy_rust_keyring_entry(name: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYRING_SERVICE, name)
        .map_err(|e| format!("runtime config keyring legacy entry failed for {name}: {e}"))
}

#[cfg(target_os = "windows")]
fn python_service_keyring_entry(name: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new_with_target(python_keyring_service_target(), KEYRING_SERVICE, name)
        .map_err(|e| format!("runtime config keyring service entry failed for {name}: {e}"))
}

#[cfg(target_os = "macos")]
fn keyring_configured(name: &str) -> Result<bool, String> {
    use security_framework::item::{CloudSync, ItemClass, ItemSearchOptions};

    const ERR_SEC_ITEM_NOT_FOUND: i32 = -25300;

    let mut options = ItemSearchOptions::new();
    options
        .class(ItemClass::generic_password())
        .service(KEYRING_SERVICE)
        .account(name)
        .cloud_sync(CloudSync::MatchSyncAny)
        .load_attributes(true)
        .limit(1);

    match options.search() {
        Ok(items) => Ok(!items.is_empty()),
        Err(e) if e.code() == ERR_SEC_ITEM_NOT_FOUND => Ok(false),
        Err(e) => Err(format!(
            "runtime config keyring status failed for {name}: {e}"
        )),
    }
}

#[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
fn keyring_configured(name: &str) -> Result<bool, String> {
    let entry = keyring_entry(name)?;
    keyring_entry_configured(&entry, name)
}

#[cfg(target_os = "windows")]
fn keyring_configured(name: &str) -> Result<bool, String> {
    let canonical = keyring_entry(name)?;
    if keyring_entry_configured_for_user(&canonical, name)? {
        return Ok(true);
    }
    let service = python_service_keyring_entry(name)?;
    if keyring_entry_configured_for_user(&service, name)? {
        return Ok(true);
    }
    let legacy = legacy_rust_keyring_entry(name)?;
    keyring_entry_configured_for_user(&legacy, name)
}

#[cfg(not(target_os = "macos"))]
fn keyring_entry_configured(entry: &keyring::Entry, name: &str) -> Result<bool, String> {
    match entry.get_password() {
        Ok(value) => Ok(!value.trim().is_empty()),
        Err(keyring::Error::NoEntry) => Ok(false),
        Err(e) => Err(format!(
            "runtime config keyring status failed for {name}: {e}"
        )),
    }
}

#[cfg(target_os = "windows")]
fn keyring_entry_configured_for_user(entry: &keyring::Entry, name: &str) -> Result<bool, String> {
    let configured = keyring_entry_configured(entry, name)?;
    if !configured {
        return Ok(false);
    }
    match entry.get_attributes() {
        Ok(attrs) => Ok(attrs
            .get("username")
            .map(|value| value == name)
            .unwrap_or(false)),
        Err(keyring::Error::NoEntry) => Ok(false),
        Err(e) => Err(format!(
            "runtime config keyring attribute read failed for {name}: {e}"
        )),
    }
}

#[cfg(target_os = "windows")]
fn delete_legacy_rust_keyring_entry(name: &str) -> Result<(), String> {
    match legacy_rust_keyring_entry(name)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!(
            "runtime config keyring legacy delete failed for {name}: {e}"
        )),
    }
}

#[cfg(target_os = "windows")]
fn delete_python_service_keyring_entry(name: &str) -> Result<(), String> {
    let entry = python_service_keyring_entry(name)?;
    if !keyring_entry_configured_for_user(&entry, name)? {
        return Ok(());
    }
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!(
            "runtime config keyring service delete failed for {name}: {e}"
        )),
    }
}

fn legacy_dotenv_configured_for(spec: &SecretSpec) -> bool {
    legacy_dotenv_paths().iter().any(|path| {
        std::iter::once(spec.name)
            .chain(spec.aliases.iter().copied())
            .any(|name| dotenv_value_configured(path, name))
    })
}

fn legacy_dotenv_paths() -> Vec<PathBuf> {
    let mut paths = Vec::new();

    if let Ok(root) = std::env::var("AGENT_FRIEND_REPO_ROOT") {
        push_dotenv_path(&mut paths, PathBuf::from(root).join(".env"));
    }

    if let Ok(cwd) = std::env::current_dir() {
        if let Some(path) = find_repo_dotenv_from(&cwd) {
            push_dotenv_path(&mut paths, path);
        }
    }

    #[cfg(debug_assertions)]
    {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        if let Some(path) = find_repo_dotenv_from(&manifest_dir) {
            push_dotenv_path(&mut paths, path);
        }
    }

    paths
}

fn push_dotenv_path(paths: &mut Vec<PathBuf>, path: PathBuf) {
    if !paths.iter().any(|existing| existing == &path) {
        paths.push(path);
    }
}

fn find_repo_dotenv_from(start: &Path) -> Option<PathBuf> {
    start
        .ancestors()
        .find(|candidate| looks_like_agent_friend_root(candidate))
        .map(|root| root.join(".env"))
}

fn looks_like_agent_friend_root(path: &Path) -> bool {
    path.join(".env.example").is_file()
        && path.join("pyproject.toml").is_file()
        && path.join("frontend").is_dir()
}

fn dotenv_value_configured(path: &Path, name: &str) -> bool {
    let Ok(raw) = fs::read_to_string(path) else {
        return false;
    };

    raw.lines().any(|line| {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            return false;
        }
        let Some((key, value)) = trimmed.split_once('=') else {
            return false;
        };
        let key = key.trim().strip_prefix("export ").unwrap_or(key.trim());
        key == name && !value.trim().trim_matches(&['"', '\''][..]).is_empty()
    })
}

fn dotenv_value(path: &Path, name: &str) -> Result<Option<String>, String> {
    if !path.is_file() {
        return Ok(None);
    }
    let raw = fs::read_to_string(path)
        .map_err(|e| format!("legacy credential file cannot be read for {name}: {e}"))?;
    Ok(raw.lines().find_map(|line| {
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
        (!value.is_empty()).then(|| value.to_string())
    }))
}

/// 仅供本 crate 在写诊断包前做精确凭据替换；值不得进入 command payload 或日志。
/// 任一已知凭据来源无法可靠读取时返回错误，让诊断导出 fail closed。
pub(crate) fn credential_redaction_values() -> Result<Vec<String>, String> {
    let mut values = Vec::new();
    for spec in SECRET_REGISTRY {
        for name in std::iter::once(spec.name).chain(spec.aliases.iter().copied()) {
            match std::env::var(name) {
                Ok(value) => push_unique_secret(&mut values, value),
                Err(std::env::VarError::NotPresent) => {}
                Err(std::env::VarError::NotUnicode(_)) => {
                    return Err(format!(
                        "environment credential {name} is not valid Unicode"
                    ));
                }
            }
            for path in legacy_dotenv_paths() {
                if let Some(value) = dotenv_value(&path, name)? {
                    push_unique_secret(&mut values, value);
                }
            }
        }

        #[cfg(target_os = "windows")]
        {
            for entry in [
                keyring_entry(spec.name),
                python_service_keyring_entry(spec.name),
                legacy_rust_keyring_entry(spec.name),
            ] {
                collect_keyring_secret(entry, spec.name, &mut values)?;
            }
        }

        #[cfg(not(target_os = "windows"))]
        collect_keyring_secret(keyring_entry(spec.name), spec.name, &mut values)?;
    }
    Ok(values)
}

/// 054：只读取正常桌面设置写入的 canonical 系统凭据；不读取 Env 或 `.env`。
pub(crate) fn migration_snapshot_managed_secrets() -> Result<BTreeMap<String, String>, String> {
    let mut result = BTreeMap::new();
    for spec in SECRET_REGISTRY {
        match keyring_entry(spec.name)?.get_password() {
            Ok(value) if !value.trim().is_empty() => {
                result.insert(spec.name.to_string(), value);
            }
            Ok(_) | Err(keyring::Error::NoEntry) => {}
            Err(error) => {
                return Err(format!(
                    "migration_keyring_read_failed for {}: {error}",
                    spec.name
                ));
            }
        }
    }
    Ok(result)
}

/// 054：以迁移快照整体替换 canonical 系统凭据，返回替换前快照供事务回滚。
pub(crate) fn migration_replace_managed_secrets(
    next: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>, String> {
    let previous = migration_snapshot_managed_secrets()?;
    for name in next.keys() {
        require_secret(name)?;
    }
    let apply = || -> Result<(), String> {
        for spec in SECRET_REGISTRY {
            let entry = keyring_entry(spec.name)?;
            if let Some(value) = next.get(spec.name) {
                entry.set_password(value).map_err(|error| {
                    format!("migration_keyring_write_failed for {}: {error}", spec.name)
                })?;
            } else {
                match entry.delete_credential() {
                    Ok(()) | Err(keyring::Error::NoEntry) => {}
                    Err(error) => {
                        return Err(format!(
                            "migration_keyring_delete_failed for {}: {error}",
                            spec.name
                        ));
                    }
                }
            }
        }
        Ok(())
    };
    if let Err(error) = apply() {
        let _ = migration_restore_managed_secrets(&previous);
        return Err(error);
    }
    Ok(previous)
}

pub(crate) fn migration_restore_managed_secrets(
    snapshot: &BTreeMap<String, String>,
) -> Result<(), String> {
    for spec in SECRET_REGISTRY {
        let entry = keyring_entry(spec.name)?;
        if let Some(value) = snapshot.get(spec.name) {
            entry.set_password(value).map_err(|error| {
                format!(
                    "migration_keyring_rollback_failed for {}: {error}",
                    spec.name
                )
            })?;
        } else {
            match entry.delete_credential() {
                Ok(()) | Err(keyring::Error::NoEntry) => {}
                Err(error) => {
                    return Err(format!(
                        "migration_keyring_rollback_failed for {}: {error}",
                        spec.name
                    ));
                }
            }
        }
    }
    Ok(())
}

fn collect_keyring_secret(
    entry: Result<keyring::Entry, String>,
    name: &str,
    values: &mut Vec<String>,
) -> Result<(), String> {
    let entry = entry?;
    match entry.get_password() {
        Ok(value) => {
            push_unique_secret(values, value);
            Ok(())
        }
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(format!(
            "keyring credential cannot be read for {name}: {error}"
        )),
    }
}

fn push_unique_secret(values: &mut Vec<String>, value: String) {
    let value = value.trim().to_string();
    if !value.is_empty() && !values.iter().any(|existing| existing == &value) {
        values.push(value);
    }
}

fn status_for(spec: &SecretSpec) -> RuntimeConfigItemStatus {
    let mut error = None;

    let env_configured = std::iter::once(spec.name)
        .chain(spec.aliases.iter().copied())
        .any(|name| {
            std::env::var(name)
                .ok()
                .map(|v| !v.trim().is_empty())
                .unwrap_or(false)
        });

    let source = if env_configured {
        Some(RuntimeConfigSource::Env)
    } else {
        match keyring_configured(spec.name) {
            Ok(true) => Some(RuntimeConfigSource::Keyring),
            Ok(false) => {
                if legacy_dotenv_configured_for(spec) {
                    Some(RuntimeConfigSource::LegacyDotenv)
                } else {
                    None
                }
            }
            Err(e) => {
                error = Some(e);
                if legacy_dotenv_configured_for(spec) {
                    Some(RuntimeConfigSource::LegacyDotenv)
                } else {
                    None
                }
            }
        }
    };

    RuntimeConfigItemStatus {
        name: spec.name.to_string(),
        kind: RuntimeConfigKind::Secret,
        group: spec.group,
        configured: source.is_some(),
        source,
        effect: spec.effect,
        error,
    }
}

#[tauri::command]
pub fn get_runtime_config_status() -> Result<RuntimeConfigSnapshot, String> {
    Ok(RuntimeConfigSnapshot {
        items: SECRET_REGISTRY.iter().map(status_for).collect(),
    })
}

#[tauri::command]
pub fn set_runtime_secret(
    payload: SetRuntimeSecretPayload,
) -> Result<RuntimeConfigMutation, String> {
    let spec = require_secret(&payload.name)?;
    let value = payload.value.trim();
    if value.is_empty() {
        return Err(format!("runtime secret {} cannot be empty", spec.name));
    }

    let entry = keyring_entry(spec.name)?;
    entry
        .set_password(value)
        .map_err(|e| format!("runtime config keyring set failed for {}: {e}", spec.name))?;

    #[cfg(target_os = "windows")]
    if let Err(e) = delete_legacy_rust_keyring_entry(spec.name) {
        log::warn!("{e}");
    }
    #[cfg(target_os = "windows")]
    if let Err(e) = delete_python_service_keyring_entry(spec.name) {
        log::warn!("{e}");
    }

    log::info!(
        "runtime_config: secret saved name={} effect={:?}",
        spec.name,
        spec.effect
    );

    Ok(RuntimeConfigMutation {
        item: status_for(spec),
        effect: spec.effect,
    })
}

#[tauri::command]
pub fn delete_runtime_secret(
    payload: DeleteRuntimeSecretPayload,
) -> Result<RuntimeConfigMutation, String> {
    let spec = require_secret(&payload.name)?;
    let entry = keyring_entry(spec.name)?;
    let mut errors = Vec::new();
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        Err(e) => errors.push(format!(
            "runtime config keyring delete failed for {}: {e}",
            spec.name
        )),
    }
    #[cfg(target_os = "windows")]
    if let Err(e) = delete_legacy_rust_keyring_entry(spec.name) {
        errors.push(e);
    }
    #[cfg(target_os = "windows")]
    if let Err(e) = delete_python_service_keyring_entry(spec.name) {
        errors.push(e);
    }
    if !errors.is_empty() {
        return Err(errors.join("; "));
    }

    log::info!(
        "runtime_config: secret deleted name={} effect={:?}",
        spec.name,
        spec.effect
    );

    Ok(RuntimeConfigMutation {
        item: status_for(spec),
        effect: spec.effect,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_only_contains_current_managed_secrets() {
        assert!(find_secret("AMAP_WEBSERVICE_KEY").is_some());
        assert!(find_secret("DEEPSEEK_API_KEY").is_some());
        assert!(find_secret("KIMI_API_KEY").is_some());
        assert!(find_secret("TAVILY_API_KEY").is_some());
        assert!(find_secret("VOLC_SPEECH_API_KEY").is_some());
        assert!(find_secret("VOLC_ARK_API_KEY").is_none());
        assert!(find_secret("OPENAI_API_KEY").is_none());
        assert!(find_secret("VOICE_BRIDGE_PUBLIC_URL").is_none());
    }

    #[test]
    fn registry_tracks_voice_bridge_env_aliases_for_volc_secrets() {
        let expected = [
            ("VOLC_ACCESS_KEY", "VOICE_BRIDGE_VOLC_ACCESS_KEY"),
            ("VOLC_SECRET_KEY", "VOICE_BRIDGE_VOLC_SECRET_KEY"),
            ("VOLC_RTC_APP_ID", "VOICE_BRIDGE_VOLC_RTC_APP_ID"),
            ("VOLC_RTC_APP_KEY", "VOICE_BRIDGE_VOLC_RTC_APP_KEY"),
            ("VOLC_SPEECH_APP_ID", "VOICE_BRIDGE_VOLC_SPEECH_APP_ID"),
            (
                "VOLC_SPEECH_ACCESS_TOKEN",
                "VOICE_BRIDGE_VOLC_SPEECH_ACCESS_TOKEN",
            ),
            ("VOLC_SPEECH_API_KEY", "VOICE_BRIDGE_VOLC_SPEECH_API_KEY"),
        ];

        for (canonical, alias) in expected {
            let spec = require_secret(canonical).expect("volc secret");
            assert!(spec.aliases.contains(&alias));
        }
    }

    #[test]
    fn windows_keyring_targets_document_python_and_legacy_rust_conventions() {
        assert_eq!(
            python_keyring_compound_target("KIMI_API_KEY"),
            "KIMI_API_KEY@agent-friend"
        );
        assert_eq!(python_keyring_service_target(), "agent-friend");
        assert_eq!(
            rust_keyring_default_target("KIMI_API_KEY"),
            "KIMI_API_KEY.agent-friend"
        );
    }

    #[test]
    fn dotenv_status_parser_detects_non_empty_value_without_exposing_it() {
        let path = std::env::temp_dir().join(format!(
            "agent-friend-runtime-config-test-{}.env",
            std::process::id()
        ));
        fs::write(
            &path,
            "DEEPSEEK_API_KEY=\"secret-value\"\nTAVILY_API_KEY=\n# VOLC_ACCESS_KEY=x\n",
        )
        .expect("write dotenv fixture");

        assert!(dotenv_value_configured(&path, "DEEPSEEK_API_KEY"));
        assert!(!dotenv_value_configured(&path, "TAVILY_API_KEY"));
        assert!(!dotenv_value_configured(&path, "VOLC_ACCESS_KEY"));
        assert_eq!(
            dotenv_value(&path, "DEEPSEEK_API_KEY").expect("read dotenv value"),
            Some("secret-value".to_string())
        );
        assert_eq!(
            dotenv_value(&path, "TAVILY_API_KEY").expect("read empty dotenv value"),
            None
        );

        let _ = fs::remove_file(path);
    }

    #[test]
    fn dotenv_status_parser_detects_voice_bridge_alias_values() {
        let path = std::env::temp_dir().join(format!(
            "agent-friend-runtime-config-alias-test-{}.env",
            std::process::id()
        ));
        fs::write(
            &path,
            "VOICE_BRIDGE_VOLC_SPEECH_API_KEY=\"secret-value\"\nVOLC_SPEECH_API_KEY=\n",
        )
        .expect("write dotenv fixture");

        assert!(dotenv_value_configured(
            &path,
            "VOICE_BRIDGE_VOLC_SPEECH_API_KEY"
        ));
        assert!(!dotenv_value_configured(&path, "VOLC_SPEECH_API_KEY"));

        let _ = fs::remove_file(path);
    }

    #[test]
    fn finds_repo_dotenv_from_nested_frontend_dir() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-runtime-config-root-{}",
            std::process::id()
        ));
        let nested = root.join("frontend").join("src-tauri");
        fs::create_dir_all(&nested).expect("create nested repo fixture");
        fs::write(root.join(".env.example"), "").expect("write env example");
        fs::write(root.join("pyproject.toml"), "").expect("write pyproject");

        assert_eq!(find_repo_dotenv_from(&nested), Some(root.join(".env")));

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn secret_effects_are_scoped_to_owning_runtime() {
        assert_eq!(
            require_secret("DEEPSEEK_API_KEY").expect("deepseek").effect,
            RuntimeConfigEffect::RestartBridge
        );
        assert_eq!(
            require_secret("KIMI_API_KEY").expect("kimi").effect,
            RuntimeConfigEffect::RestartBridge
        );
        assert_eq!(
            require_secret("VOLC_RTC_APP_KEY").expect("volc").effect,
            RuntimeConfigEffect::RestartVoice
        );
    }
}
