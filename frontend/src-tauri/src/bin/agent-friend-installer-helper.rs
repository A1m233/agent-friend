#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use app_lib::install_contract::{self, ActiveInstallReceipt};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::{BTreeMap, HashSet},
    env, fs,
    io::Write,
    path::{Component, Path, PathBuf},
};

const TRANSACTIONS_DIR: &str = ".installer-transactions";

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct TransactionState {
    schema_version: u32,
    transaction_id: String,
    main_binary_name: String,
    main_existed: bool,
    receipt_existed: bool,
    #[serde(default)]
    registry_snapshot: Option<RegistrySnapshot>,
    #[serde(default)]
    shortcut_snapshots: Vec<ShortcutSnapshot>,
    #[serde(default)]
    move_source_root: Option<PathBuf>,
    #[serde(default)]
    move_source_backup: Option<PathBuf>,
    #[serde(default)]
    source_preserved_paths: Vec<PathBuf>,
    #[serde(default)]
    target_preserved_paths: Vec<PathBuf>,
    #[serde(default)]
    target_existed_before_install: bool,
    committed: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ShortcutSnapshot {
    path: PathBuf,
    existed: bool,
    backup_name: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RegistrySnapshot {
    uninstall_key: RegistryKeySnapshot,
    manufacturer_key: RegistryKeySnapshot,
    autostart_value: RegistryValueSnapshot,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RegistryKeySnapshot {
    path: String,
    existed: bool,
    values: BTreeMap<String, RawRegistryValue>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RegistryValueSnapshot {
    key_path: String,
    value_name: String,
    existed: bool,
    value: Option<RawRegistryValue>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RawRegistryValue {
    value_type: u32,
    bytes: Vec<u8>,
}

fn required_arg(args: &[String], name: &str) -> Result<String, String> {
    args.windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].clone())
        .ok_or_else(|| format!("missing required argument: {name}"))
}

fn optional_arg(args: &[String], name: &str) -> Option<String> {
    args.windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].clone())
        .filter(|value| !value.is_empty())
}

fn required_bool_arg(args: &[String], name: &str) -> Result<bool, String> {
    match required_arg(args, name)?.as_str() {
        "0" => Ok(false),
        "1" => Ok(true),
        value => Err(format!("invalid boolean argument {name}: {value:?}")),
    }
}

fn validate_token(value: &str, label: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err(format!("invalid {label}: {value:?}"));
    }
    Ok(())
}

fn validate_registry_component(value: &str, label: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 128
        || value.chars().any(|character| {
            character.is_control() || matches!(character, '\\' | '/' | ':' | '*' | '?' | '"')
        })
    {
        return Err(format!("invalid {label}: {value:?}"));
    }
    Ok(())
}

fn transaction_root(install_root: &Path, transaction_id: &str) -> Result<PathBuf, String> {
    validate_token(transaction_id, "transaction id")?;
    Ok(install_root.join(TRANSACTIONS_DIR).join(transaction_id))
}

fn safe_join(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let relative = Path::new(relative);
    if relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(format!(
            "unsafe installer-relative path: {}",
            relative.display()
        ));
    }
    let path = root.join(relative);
    let canonical = path.canonicalize().map_err(|error| error.to_string())?;
    if !canonical.starts_with(root) {
        return Err("installer-relative path escaped install root".to_string());
    }
    Ok(canonical)
}

fn read_state(path: &Path) -> Result<TransactionState, String> {
    serde_json::from_slice(
        &fs::read(path.join("state.json"))
            .map_err(|error| format!("transaction state unreadable: {error}"))?,
    )
    .map_err(|error| format!("transaction state invalid: {error}"))
}

fn write_state(path: &Path, state: &TransactionState) -> Result<(), String> {
    let state_path = path.join("state.json");
    let bytes = serde_json::to_vec(state).map_err(|error| error.to_string())?;
    write_and_sync(&state_path, &bytes)
        .map_err(|error| format!("transaction state write failed: {error}"))
}

fn write_and_sync(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut file = fs::File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}

fn validate_shortcut_path(path: &Path) -> Result<(), String> {
    if !path.is_absolute()
        || !path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("lnk"))
    {
        return Err(format!("invalid shortcut path: {}", path.display()));
    }
    Ok(())
}

fn snapshot_shortcuts(
    preparing: &Path,
    paths: &[PathBuf],
) -> Result<Vec<ShortcutSnapshot>, String> {
    if paths.len() > 3 {
        return Err("too many shortcut paths".to_string());
    }
    let mut snapshots = Vec::new();
    for (index, path) in paths.iter().enumerate() {
        validate_shortcut_path(path)?;
        let backup_name = format!("shortcut-{index}.lnk.backup");
        let existed = path.is_file();
        if existed {
            fs::copy(path, preparing.join(&backup_name))
                .map_err(|error| format!("shortcut snapshot failed: {error}"))?;
        }
        snapshots.push(ShortcutSnapshot {
            path: path.clone(),
            existed,
            backup_name,
        });
    }
    Ok(snapshots)
}

fn restore_shortcuts(transaction: &Path, snapshots: &[ShortcutSnapshot]) -> Result<(), String> {
    for snapshot in snapshots {
        validate_shortcut_path(&snapshot.path)?;
        if snapshot.existed {
            if let Some(parent) = snapshot.path.parent() {
                fs::create_dir_all(parent)
                    .map_err(|error| format!("shortcut rollback directory failed: {error}"))?;
            }
            fs::copy(transaction.join(&snapshot.backup_name), &snapshot.path)
                .map_err(|error| format!("shortcut rollback failed: {error}"))?;
        } else {
            match fs::remove_file(&snapshot.path) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(format!("shortcut rollback delete failed: {error}")),
            }
        }
    }
    Ok(())
}

fn rollback_transaction(install_root: &Path, path: &Path) -> Result<(), String> {
    let state = read_state(path)?;
    validate_token(&state.main_binary_name, "main binary name")?;
    let main = install_root.join(format!("{}.exe", state.main_binary_name));
    let receipt = install_root.join(install_contract::ACTIVE_INSTALL_FILE);
    if state.committed {
        if let Some(backup) = &state.move_source_backup {
            if backup.exists() {
                fs::remove_dir_all(backup).map_err(|error| {
                    format!("committed migration backup cleanup failed: {error}")
                })?;
            }
        }
        fs::remove_dir_all(path)
            .map_err(|error| format!("committed transaction cleanup failed: {error}"))?;
        return Ok(());
    }
    if let (Some(source), Some(backup)) = (&state.move_source_root, &state.move_source_backup) {
        if backup.exists() {
            restore_preserved_paths_to_backup(source, backup, &state.source_preserved_paths)?;
            fs::rename(backup, source)
                .map_err(|error| format!("migration source rollback failed: {error}"))?;
        }
    }
    if state.main_existed {
        fs::copy(path.join("main.exe.backup"), &main)
            .map_err(|error| format!("main binary rollback failed: {error}"))?;
    } else {
        let _ = fs::remove_file(&main);
    }
    if state.receipt_existed {
        fs::copy(path.join("active-install.json.backup"), &receipt)
            .map_err(|error| format!("active receipt rollback failed: {error}"))?;
    } else {
        let _ = fs::remove_file(&receipt);
    }
    if let Some(snapshot) = &state.registry_snapshot {
        restore_registry(snapshot)?;
    }
    restore_shortcuts(path, &state.shortcut_snapshots)?;
    if state.move_source_root.is_some() {
        remove_program_root_preserving(install_root, &state.target_preserved_paths)?;
        return Ok(());
    }
    fs::remove_dir_all(path)
        .map_err(|error| format!("rolled back transaction cleanup failed: {error}"))?;
    Ok(())
}

fn recover_incomplete_transactions(install_root: &Path) -> Result<Vec<String>, String> {
    let root = install_root.join(TRANSACTIONS_DIR);
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let entries = fs::read_dir(&root)
        .map_err(|error| error.to_string())?
        .map(|entry| {
            entry
                .map(|value| value.path())
                .map_err(|error| error.to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    let active_count = entries
        .iter()
        .filter(|path| {
            !path
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with(".preparing-"))
        })
        .count();
    if active_count > 1 {
        return Err("multiple installer transactions require manual recovery".to_string());
    }
    let mut recovered = Vec::new();
    for path in entries.iter().filter(|path| {
        path.file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.starts_with(".preparing-"))
    }) {
        if !path.is_dir() {
            return Err("unexpected file in installer transaction directory".to_string());
        }
        if !path
            .canonicalize()
            .map_err(|error| error.to_string())?
            .starts_with(install_root)
        {
            return Err("installer transaction escaped install root".to_string());
        }
        fs::remove_dir_all(path)
            .map_err(|error| format!("preparing transaction cleanup failed: {error}"))?;
    }
    for path in entries.iter().filter(|path| {
        !path
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.starts_with(".preparing-"))
    }) {
        if !path.is_dir() {
            return Err("unexpected file in installer transaction directory".to_string());
        }
        let state = read_state(&path)?;
        recovered.push(state.transaction_id.clone());
        rollback_transaction(install_root, path)?;
    }
    let _ = fs::remove_dir(&root);
    Ok(recovered)
}

fn begin_transaction(
    install_root: &Path,
    transaction_id: &str,
    main_binary_name: &str,
    registry_product_name: &str,
    manufacturer: &str,
    autostart_value_name: &str,
    shortcut_paths: &[PathBuf],
    move_source_root: Option<PathBuf>,
    source_preserved_paths: Vec<PathBuf>,
    target_preserved_paths: Vec<PathBuf>,
    target_existed_before_install: bool,
) -> Result<(), String> {
    validate_token(main_binary_name, "main binary name")?;
    validate_registry_component(registry_product_name, "registry product name")?;
    validate_registry_component(manufacturer, "manufacturer")?;
    validate_registry_component(autostart_value_name, "autostart value name")?;
    let path = transaction_root(install_root, transaction_id)?;
    if path.exists() {
        return Err("transaction already exists".to_string());
    }
    let transactions = install_root.join(TRANSACTIONS_DIR);
    fs::create_dir_all(&transactions).map_err(|error| error.to_string())?;
    let preparing = transactions.join(format!(".preparing-{transaction_id}"));
    if preparing.exists() {
        return Err("preparing transaction already exists".to_string());
    }
    fs::create_dir(&preparing).map_err(|error| error.to_string())?;
    let main = install_root.join(format!("{main_binary_name}.exe"));
    let receipt = install_root.join(install_contract::ACTIVE_INSTALL_FILE);
    let prepare_result = (|| -> Result<(), String> {
        if main.is_file() {
            fs::copy(&main, preparing.join("main.exe.backup"))
                .map_err(|error| error.to_string())?;
        }
        if receipt.is_file() {
            fs::copy(&receipt, preparing.join("active-install.json.backup"))
                .map_err(|error| error.to_string())?;
        }
        let registry_snapshot =
            snapshot_registry(registry_product_name, manufacturer, autostart_value_name)?;
        let shortcut_snapshots = snapshot_shortcuts(&preparing, shortcut_paths)?;
        write_state(
            &preparing,
            &TransactionState {
                schema_version: 1,
                transaction_id: transaction_id.to_string(),
                main_binary_name: main_binary_name.to_string(),
                main_existed: main.is_file(),
                receipt_existed: receipt.is_file(),
                registry_snapshot: Some(registry_snapshot),
                shortcut_snapshots,
                move_source_root,
                move_source_backup: None,
                source_preserved_paths,
                target_preserved_paths,
                target_existed_before_install,
                committed: false,
            },
        )?;
        fs::rename(&preparing, &path)
            .map_err(|error| format!("transaction publish failed: {error}"))
    })();
    if prepare_result.is_err() {
        let _ = fs::remove_dir_all(&preparing);
    }
    prepare_result
}

#[cfg(target_os = "windows")]
fn snapshot_registry(
    product_name: &str,
    manufacturer: &str,
    autostart_value_name: &str,
) -> Result<RegistrySnapshot, String> {
    let uninstall_path =
        format!("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{product_name}");
    let manufacturer_path = format!("Software\\{manufacturer}\\{product_name}");
    let run_path = "Software\\Microsoft\\Windows\\CurrentVersion\\Run".to_string();
    Ok(RegistrySnapshot {
        uninstall_key: snapshot_registry_key(&uninstall_path)?,
        manufacturer_key: snapshot_registry_key(&manufacturer_path)?,
        autostart_value: snapshot_registry_value(&run_path, autostart_value_name)?,
    })
}

#[cfg(not(target_os = "windows"))]
fn snapshot_registry(
    _product_name: &str,
    _manufacturer: &str,
    _autostart_value_name: &str,
) -> Result<RegistrySnapshot, String> {
    Err("installer helper is only supported on Windows".to_string())
}

#[cfg(target_os = "windows")]
fn snapshot_registry_key(path: &str) -> Result<RegistryKeySnapshot, String> {
    use std::io::ErrorKind;
    use winreg::{enums::HKEY_CURRENT_USER, RegKey};

    let hive = RegKey::predef(HKEY_CURRENT_USER);
    let key = match hive.open_subkey(path) {
        Ok(key) => key,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Ok(RegistryKeySnapshot {
                path: path.to_string(),
                existed: false,
                values: BTreeMap::new(),
            });
        }
        Err(error) => return Err(format!("registry_snapshot_open_failed: {error}")),
    };
    let values = key
        .enum_values()
        .map(|item| {
            let (name, value) = item.map_err(|error| error.to_string())?;
            Ok((
                name,
                RawRegistryValue {
                    value_type: value.vtype as u32,
                    bytes: value.bytes,
                },
            ))
        })
        .collect::<Result<BTreeMap<_, _>, String>>()?;
    Ok(RegistryKeySnapshot {
        path: path.to_string(),
        existed: true,
        values,
    })
}

#[cfg(target_os = "windows")]
fn snapshot_registry_value(
    key_path: &str,
    value_name: &str,
) -> Result<RegistryValueSnapshot, String> {
    use std::io::ErrorKind;
    use winreg::{enums::HKEY_CURRENT_USER, RegKey};

    let hive = RegKey::predef(HKEY_CURRENT_USER);
    let value = match hive.open_subkey(key_path) {
        Ok(key) => match key.get_raw_value(value_name) {
            Ok(value) => Some(RawRegistryValue {
                value_type: value.vtype as u32,
                bytes: value.bytes,
            }),
            Err(error) if error.kind() == ErrorKind::NotFound => None,
            Err(error) => return Err(format!("registry_snapshot_value_failed: {error}")),
        },
        Err(error) if error.kind() == ErrorKind::NotFound => None,
        Err(error) => return Err(format!("registry_snapshot_open_failed: {error}")),
    };
    Ok(RegistryValueSnapshot {
        key_path: key_path.to_string(),
        value_name: value_name.to_string(),
        existed: value.is_some(),
        value,
    })
}

#[cfg(target_os = "windows")]
fn restore_registry(snapshot: &RegistrySnapshot) -> Result<(), String> {
    restore_registry_key(&snapshot.uninstall_key)?;
    restore_registry_key(&snapshot.manufacturer_key)?;
    restore_registry_value(&snapshot.autostart_value)
}

#[cfg(not(target_os = "windows"))]
fn restore_registry(_snapshot: &RegistrySnapshot) -> Result<(), String> {
    Err("installer helper is only supported on Windows".to_string())
}

#[cfg(target_os = "windows")]
fn restore_registry_key(snapshot: &RegistryKeySnapshot) -> Result<(), String> {
    use std::io::ErrorKind;
    use winreg::{enums::HKEY_CURRENT_USER, RegKey};

    let hive = RegKey::predef(HKEY_CURRENT_USER);
    if !snapshot.existed {
        return match hive.delete_subkey_all(&snapshot.path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
            Err(error) => Err(format!("registry_rollback_delete_failed: {error}")),
        };
    }
    let (key, _) = hive
        .create_subkey(&snapshot.path)
        .map_err(|error| format!("registry_rollback_create_failed: {error}"))?;
    let current_names = key
        .enum_values()
        .map(|item| {
            item.map(|(name, _)| name)
                .map_err(|error| error.to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    for name in current_names {
        key.delete_value(name)
            .map_err(|error| format!("registry_rollback_clear_failed: {error}"))?;
    }
    for (name, value) in &snapshot.values {
        key.set_raw_value(name, &to_reg_value(value)?)
            .map_err(|error| format!("registry_rollback_value_failed: {error}"))?;
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn restore_registry_value(snapshot: &RegistryValueSnapshot) -> Result<(), String> {
    use std::io::ErrorKind;
    use winreg::{enums::HKEY_CURRENT_USER, RegKey};

    let hive = RegKey::predef(HKEY_CURRENT_USER);
    let (key, _) = hive
        .create_subkey(&snapshot.key_path)
        .map_err(|error| format!("registry_rollback_create_failed: {error}"))?;
    if snapshot.existed {
        let value = snapshot
            .value
            .as_ref()
            .ok_or("registry rollback snapshot is missing its value")?;
        key.set_raw_value(&snapshot.value_name, &to_reg_value(value)?)
            .map_err(|error| format!("registry_rollback_value_failed: {error}"))
    } else {
        match key.delete_value(&snapshot.value_name) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
            Err(error) => Err(format!("registry_rollback_delete_value_failed: {error}")),
        }
    }
}

#[cfg(target_os = "windows")]
fn to_reg_value(value: &RawRegistryValue) -> Result<winreg::RegValue, String> {
    use winreg::enums::*;
    let vtype = match value.value_type {
        value if value == REG_NONE as u32 => REG_NONE,
        value if value == REG_SZ as u32 => REG_SZ,
        value if value == REG_EXPAND_SZ as u32 => REG_EXPAND_SZ,
        value if value == REG_BINARY as u32 => REG_BINARY,
        value if value == REG_DWORD as u32 => REG_DWORD,
        value if value == REG_DWORD_BIG_ENDIAN as u32 => REG_DWORD_BIG_ENDIAN,
        value if value == REG_LINK as u32 => REG_LINK,
        value if value == REG_MULTI_SZ as u32 => REG_MULTI_SZ,
        value if value == REG_RESOURCE_LIST as u32 => REG_RESOURCE_LIST,
        value if value == REG_FULL_RESOURCE_DESCRIPTOR as u32 => REG_FULL_RESOURCE_DESCRIPTOR,
        value if value == REG_RESOURCE_REQUIREMENTS_LIST as u32 => REG_RESOURCE_REQUIREMENTS_LIST,
        value if value == REG_QWORD as u32 => REG_QWORD,
        other => {
            return Err(format!(
                "unsupported registry value type in rollback: {other}"
            ))
        }
    };
    Ok(winreg::RegValue {
        bytes: value.bytes.clone(),
        vtype,
    })
}

fn active_payload_executables(
    install_root: &Path,
    expected_profile_id: &str,
) -> Result<HashSet<PathBuf>, String> {
    let mut allowed = HashSet::new();
    let receipt_path = install_root.join(install_contract::ACTIVE_INSTALL_FILE);
    if !receipt_path.is_file() {
        return Ok(allowed);
    }
    let receipt: ActiveInstallReceipt =
        serde_json::from_slice(&fs::read(&receipt_path).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
    if receipt.profile_id != expected_profile_id {
        return Err("installer_profile_receipt_mismatch".to_string());
    }
    let payload = safe_join(install_root, &receipt.payload_relative_path)?;
    let manifest: serde_json::Value = serde_json::from_slice(
        &fs::read(payload.join("payload-manifest.json")).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    if let Some(files) = manifest.get("files").and_then(|value| value.as_array()) {
        for item in files {
            let Some(relative) = item.get("path").and_then(|value| value.as_str()) else {
                continue;
            };
            if relative.to_ascii_lowercase().ends_with(".exe") {
                if let Ok(path) = safe_join(&payload, relative) {
                    if path.starts_with(&payload) {
                        allowed.insert(path);
                    }
                }
            }
        }
    }
    Ok(allowed)
}

#[cfg(target_os = "windows")]
fn quiesce_blockers(
    install_root: &Path,
    main_binary_name: &str,
    profile_id: &str,
) -> Result<Vec<u32>, String> {
    use std::os::windows::ffi::OsStrExt;
    use windows::{
        core::{HRESULT, PCWSTR, PWSTR},
        Win32::{
            Foundation::{
                CloseHandle, ERROR_NO_MORE_FILES, ERROR_SUCCESS, WAIT_OBJECT_0, WAIT_TIMEOUT,
            },
            System::{
                Diagnostics::ToolHelp::{
                    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
                    TH32CS_SNAPPROCESS,
                },
                RestartManager::{RmEndSession, RmRegisterResources, RmShutdown, RmStartSession},
                Threading::{
                    OpenProcess, QueryFullProcessImageNameW, TerminateProcess, WaitForSingleObject,
                    PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
                    PROCESS_TERMINATE,
                },
            },
        },
    };

    struct Session(u32);
    impl Drop for Session {
        fn drop(&mut self) {
            unsafe {
                let _ = RmEndSession(self.0);
            }
        }
    }

    struct OwnedProcess {
        pid: u32,
        handle: windows::Win32::Foundation::HANDLE,
    }
    impl Drop for OwnedProcess {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseHandle(self.handle);
            }
        }
    }

    fn list(
        session: u32,
    ) -> Result<Vec<windows::Win32::System::RestartManager::RM_PROCESS_INFO>, String> {
        use windows::Win32::{
            Foundation::{ERROR_MORE_DATA, ERROR_SUCCESS},
            System::RestartManager::{RmGetList, RM_PROCESS_INFO},
        };
        let mut needed = 0;
        let mut count = 0;
        let mut reasons = 0;
        let first = unsafe { RmGetList(session, &mut needed, &mut count, None, &mut reasons) };
        if first == ERROR_SUCCESS && needed == 0 {
            return Ok(Vec::new());
        }
        if first != ERROR_MORE_DATA {
            return Err(format!("restart_manager_list_failed: {}", first.0));
        }
        let mut entries = vec![RM_PROCESS_INFO::default(); needed as usize];
        count = needed;
        let result = unsafe {
            RmGetList(
                session,
                &mut needed,
                &mut count,
                Some(entries.as_mut_ptr()),
                &mut reasons,
            )
        };
        if result != ERROR_SUCCESS {
            return Err(format!("restart_manager_list_failed: {}", result.0));
        }
        entries.truncate(count as usize);
        Ok(entries)
    }

    fn process_path_from_handle(
        handle: windows::Win32::Foundation::HANDLE,
        pid: u32,
    ) -> Result<PathBuf, String> {
        let mut buffer = vec![0u16; 32768];
        let mut length = buffer.len() as u32;
        let result = unsafe {
            QueryFullProcessImageNameW(
                handle,
                PROCESS_NAME_WIN32,
                PWSTR(buffer.as_mut_ptr()),
                &mut length,
            )
        };
        result.map_err(|error| format!("blocker_process_path_failed: {pid}: {error}"))?;
        Ok(PathBuf::from(String::from_utf16_lossy(
            &buffer[..length as usize],
        )))
    }

    fn process_path(pid: u32) -> Result<PathBuf, String> {
        let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }
            .map_err(|error| format!("blocker_process_open_failed: {pid}: {error}"))?;
        let result = process_path_from_handle(handle, pid);
        unsafe {
            let _ = CloseHandle(handle);
        }
        result
    }

    fn is_allowed_path(allowed: &HashSet<PathBuf>, candidate: &Path) -> bool {
        let candidate = candidate.to_string_lossy();
        allowed
            .iter()
            .any(|path| path.to_string_lossy().eq_ignore_ascii_case(&candidate))
    }

    fn owned_process_ids(allowed: &HashSet<PathBuf>) -> Result<Vec<u32>, String> {
        struct Snapshot(windows::Win32::Foundation::HANDLE);
        impl Drop for Snapshot {
            fn drop(&mut self) {
                unsafe {
                    let _ = CloseHandle(self.0);
                }
            }
        }

        let allowed_names = allowed
            .iter()
            .filter_map(|path| path.file_name())
            .map(|name| name.to_string_lossy().to_ascii_lowercase())
            .collect::<HashSet<_>>();
        let snapshot = Snapshot(
            unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) }
                .map_err(|error| format!("process_snapshot_failed: {error}"))?,
        );
        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        let mut first = true;
        let mut owned = Vec::new();
        loop {
            let next = if first {
                first = false;
                unsafe { Process32FirstW(snapshot.0, &mut entry) }
            } else {
                unsafe { Process32NextW(snapshot.0, &mut entry) }
            };
            if let Err(error) = next {
                if error.code() == HRESULT::from_win32(ERROR_NO_MORE_FILES.0) {
                    break;
                }
                return Err(format!("process_snapshot_enumeration_failed: {error}"));
            }
            let pid = entry.th32ProcessID;
            if pid == 0 || pid == std::process::id() {
                continue;
            }
            match process_path(pid) {
                Ok(path) => {
                    let canonical = path.canonicalize().unwrap_or(path);
                    if is_allowed_path(allowed, &canonical) {
                        owned.push(pid);
                    }
                }
                Err(error) => {
                    let end = entry
                        .szExeFile
                        .iter()
                        .position(|value| *value == 0)
                        .unwrap_or(entry.szExeFile.len());
                    let name =
                        String::from_utf16_lossy(&entry.szExeFile[..end]).to_ascii_lowercase();
                    if allowed_names.contains(&name) {
                        return Err(format!(
                            "blocker_ownership_unconfirmed: pid={pid} name={name}: {error}"
                        ));
                    }
                }
            }
        }
        owned.sort_unstable();
        owned.dedup();
        Ok(owned)
    }

    fn open_owned_processes(allowed: &HashSet<PathBuf>) -> Result<Vec<OwnedProcess>, String> {
        let mut processes = Vec::new();
        for pid in owned_process_ids(allowed)? {
            let handle = unsafe {
                OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE | PROCESS_TERMINATE,
                    false,
                    pid,
                )
            }
            .map_err(|error| format!("owned_blocker_open_failed: {pid}: {error}"))?;
            let process = OwnedProcess { pid, handle };
            let path = process_path_from_handle(process.handle, pid)?
                .canonicalize()
                .map_err(|error| error.to_string())?;
            if !is_allowed_path(allowed, &path) {
                return Err(format!(
                    "blocker_ownership_unconfirmed: pid={pid} path={}",
                    path.display()
                ));
            }
            processes.push(process);
        }
        Ok(processes)
    }

    let mut files = Vec::new();
    let mut allowed = active_payload_executables(install_root, profile_id)?;
    for path in [
        install_root.join(format!("{main_binary_name}.exe")),
        install_root.join("runtime/python/agent-friend-runtime.exe"),
    ] {
        if path.is_file() {
            let canonical = path.canonicalize().map_err(|error| error.to_string())?;
            allowed.insert(canonical.clone());
            files.push(canonical);
        }
    }
    for path in &allowed {
        if path.is_file() {
            files.push(path.clone());
        }
    }
    if files.is_empty() {
        return Ok(Vec::new());
    }

    let mut session_handle = 0;
    let mut session_key = [0u16; 33];
    let start =
        unsafe { RmStartSession(&mut session_handle, None, PWSTR(session_key.as_mut_ptr())) };
    if start != ERROR_SUCCESS {
        return Err(format!("restart_manager_start_failed: {}", start.0));
    }
    let session = Session(session_handle);
    let wide_files: Vec<Vec<u16>> = files
        .iter()
        .map(|path| {
            path.as_os_str()
                .encode_wide()
                .chain(std::iter::once(0))
                .collect()
        })
        .collect();
    let pointers: Vec<PCWSTR> = wide_files
        .iter()
        .map(|value| PCWSTR(value.as_ptr()))
        .collect();
    let register = unsafe { RmRegisterResources(session.0, Some(&pointers), None, None) };
    if register != ERROR_SUCCESS {
        return Err(format!("restart_manager_register_failed: {}", register.0));
    }
    let blockers = list(session.0)?;
    for blocker in &blockers {
        let path = process_path(blocker.Process.dwProcessId)?
            .canonicalize()
            .map_err(|error| error.to_string())?;
        if !is_allowed_path(&allowed, &path) {
            return Err(format!(
                "blocker_ownership_unconfirmed: pid={} path={}",
                blocker.Process.dwProcessId,
                path.display()
            ));
        }
    }
    // Open and verify handles before asking Restart Manager to shut anything
    // down. During console-process teardown, reopening the same PID can return
    // access denied even though the already-verified process is exiting.
    let owned = open_owned_processes(&allowed)?;
    if !blockers.is_empty() {
        // Graceful Restart Manager shutdown is best-effort. Console services may
        // reject it; the verified PID + exact image-path fallback below is still safe.
        let _ = unsafe { RmShutdown(session.0, 0, None) };
    }
    // Restart Manager does not consistently report PyInstaller one-file
    // processes as file users. Enumerate all processes as a second discovery
    // path, then retain only exact canonical executable paths already proven to
    // belong to this install profile.
    let mut terminated = Vec::new();
    for process in &owned {
        let graceful_wait = unsafe { WaitForSingleObject(process.handle, 1500) };
        if graceful_wait == WAIT_OBJECT_0 {
            continue;
        }
        if graceful_wait != WAIT_TIMEOUT {
            return Err(format!(
                "owned_blocker_wait_failed: pid={} wait={}",
                process.pid, graceful_wait.0
            ));
        }
        unsafe { TerminateProcess(process.handle, 32) }
            .map_err(|error| format!("owned_blocker_terminate_failed: {}: {error}", process.pid))?;
        let wait = unsafe { WaitForSingleObject(process.handle, 5000) };
        if wait != WAIT_OBJECT_0 {
            return Err(format!(
                "owned_blocker_wait_failed: pid={} wait={}",
                process.pid, wait.0
            ));
        }
        terminated.push(process.pid);
    }
    if !owned_process_ids(&allowed)?.is_empty() {
        return Err("owned_blocker_remained_after_shutdown".to_string());
    }
    Ok(terminated)
}

#[cfg(not(target_os = "windows"))]
fn quiesce_blockers(
    _install_root: &Path,
    _main_binary_name: &str,
    _profile_id: &str,
) -> Result<Vec<u32>, String> {
    Err("installer helper is only supported on Windows".to_string())
}

#[cfg(target_os = "windows")]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows::{
        core::PCWSTR,
        Win32::Storage::FileSystem::{
            MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        },
    };
    let source: Vec<u16> = source
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        MoveFileExW(
            PCWSTR(source.as_ptr()),
            PCWSTR(destination.as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    }
    .map_err(|error| format!("active receipt replace failed: {error}"))
}

#[cfg(not(target_os = "windows"))]
fn atomic_replace(source: &Path, destination: &Path) -> Result<(), String> {
    fs::rename(source, destination).map_err(|error| error.to_string())
}

fn validate_preserved_relative_path(relative: &Path) -> Result<(), String> {
    if relative.as_os_str().is_empty()
        || relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(format!(
            "invalid preserved installer path: {}",
            relative.display()
        ));
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn paths_equal(left: &Path, right: &Path) -> bool {
    let normalize = |path: &Path| {
        let value = path.to_string_lossy().replace('/', "\\");
        let value = if let Some(unc) = value.strip_prefix(r"\\?\UNC\") {
            format!(r"\\{unc}")
        } else if let Some(local) = value.strip_prefix(r"\\?\") {
            local.to_string()
        } else {
            value
        };
        value.trim_end_matches('\\').to_lowercase()
    };
    normalize(left) == normalize(right)
}

#[cfg(not(target_os = "windows"))]
fn paths_equal(left: &Path, right: &Path) -> bool {
    left == right
}

fn protected_paths_for_root(
    profile: &install_contract::InstallProfile,
    root: &Path,
) -> Result<Vec<PathBuf>, String> {
    let local_data = dirs::data_local_dir().ok_or("local app data directory is unavailable")?;
    let log_parent = local_data.join(&profile.log_namespace);
    if paths_equal(root, &log_parent) {
        Ok(vec![PathBuf::from("Logs")])
    } else {
        Ok(Vec::new())
    }
}

fn root_contains_only_preserved_paths(root: &Path, preserved: &[PathBuf]) -> Result<bool, String> {
    for relative in preserved {
        validate_preserved_relative_path(relative)?;
    }
    let allowed_top_level = preserved
        .iter()
        .filter_map(|path| path.components().next())
        .map(|component| component.as_os_str().to_os_string())
        .collect::<HashSet<_>>();
    for entry in fs::read_dir(root).map_err(|error| format!("install root unreadable: {error}"))? {
        let entry = entry.map_err(|error| format!("install root unreadable: {error}"))?;
        if !allowed_top_level.contains(&entry.file_name()) {
            return Ok(false);
        }
    }
    Ok(true)
}

fn move_preserved_paths_from_backup(
    source: &Path,
    backup: &Path,
    preserved: &[PathBuf],
) -> Result<(), String> {
    for relative in preserved {
        validate_preserved_relative_path(relative)?;
        let from = backup.join(relative);
        if !from.exists() {
            continue;
        }
        let destination = source.join(relative);
        let parent = destination
            .parent()
            .ok_or("preserved source path has no parent")?;
        fs::create_dir_all(parent)
            .map_err(|error| format!("preserved source directory failed: {error}"))?;
        fs::rename(&from, &destination)
            .map_err(|error| format!("preserved source path restore failed: {error}"))?;
    }
    Ok(())
}

fn restore_preserved_paths_to_backup(
    source: &Path,
    backup: &Path,
    preserved: &[PathBuf],
) -> Result<(), String> {
    if source.exists() {
        for relative in preserved {
            validate_preserved_relative_path(relative)?;
            let from = source.join(relative);
            if !from.exists() {
                continue;
            }
            let destination = backup.join(relative);
            if destination.exists() {
                return Err(format!(
                    "migration rollback found duplicate preserved path: {}",
                    relative.display()
                ));
            }
            let parent = destination
                .parent()
                .ok_or("preserved backup path has no parent")?;
            fs::create_dir_all(parent)
                .map_err(|error| format!("preserved backup directory failed: {error}"))?;
            fs::rename(&from, &destination)
                .map_err(|error| format!("preserved path rollback failed: {error}"))?;
        }
        if !root_contains_only_preserved_paths(source, &[])? {
            return Err(
                "migration rollback found unexpected files in retired source root".to_string(),
            );
        }
        fs::remove_dir(source)
            .map_err(|error| format!("retired source placeholder cleanup failed: {error}"))?;
    }
    Ok(())
}

fn remove_program_root_preserving(root: &Path, preserved: &[PathBuf]) -> Result<(), String> {
    for relative in preserved {
        validate_preserved_relative_path(relative)?;
    }
    let allowed_top_level = preserved
        .iter()
        .filter_map(|path| path.components().next())
        .map(|component| component.as_os_str().to_os_string())
        .collect::<HashSet<_>>();
    for entry in fs::read_dir(root).map_err(|error| format!("install root unreadable: {error}"))? {
        let entry = entry.map_err(|error| format!("install root unreadable: {error}"))?;
        if allowed_top_level.contains(&entry.file_name()) {
            continue;
        }
        let path = entry.path();
        if path.is_dir() {
            fs::remove_dir_all(&path)
        } else {
            fs::remove_file(&path)
        }
        .map_err(|error| format!("migration target rollback failed: {error}"))?;
    }
    if root_contains_only_preserved_paths(root, &[])? {
        fs::remove_dir(root)
            .map_err(|error| format!("migration target cleanup failed: {error}"))?;
    }
    Ok(())
}

fn roots_overlap(left: &Path, right: &Path) -> bool {
    left.starts_with(right) || right.starts_with(left)
}

fn preinstall(args: &[String]) -> Result<serde_json::Value, String> {
    let target_existed_before_install = required_bool_arg(args, "--target-existed-before-install")?;
    let move_intent = required_bool_arg(args, "--move-intent")?;
    let registered_root = optional_arg(args, "--registered-root").map(PathBuf::from);
    let shortcut_paths = [
        optional_arg(args, "--start-menu-shortcut"),
        optional_arg(args, "--legacy-start-menu-shortcut"),
        optional_arg(args, "--desktop-shortcut"),
    ]
    .into_iter()
    .flatten()
    .map(PathBuf::from)
    .collect::<Vec<_>>();
    let install_root = PathBuf::from(required_arg(args, "--install-root")?);
    fs::create_dir_all(&install_root).map_err(|error| error.to_string())?;
    let mut install_root = install_root
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let transaction_id = required_arg(args, "--transaction-id")?;
    let main_binary_name = required_arg(args, "--main-binary-name")?;
    let registry_product_name = required_arg(args, "--registry-product-name")?;
    let manufacturer = required_arg(args, "--manufacturer")?;
    let autostart_value_name = required_arg(args, "--autostart-value-name")?;
    let profile = install_contract::embedded_profile()?;
    if main_binary_name != profile.main_binary_name
        || registry_product_name != profile.registry_product_name
        || autostart_value_name != profile.autostart_value_name
    {
        return Err("installer_profile_argument_mismatch".to_string());
    }
    if profile.profile_id == "installer-smoke"
        && install_root.file_name().and_then(|name| name.to_str())
            != Some(profile.install_dir_name.as_str())
    {
        return Err("installer_smoke_root_mismatch".to_string());
    }
    let recovered = recover_incomplete_transactions(&install_root)?;
    if !install_root.exists() {
        fs::create_dir_all(&install_root).map_err(|error| error.to_string())?;
        install_root = install_root
            .canonicalize()
            .map_err(|error| error.to_string())?;
    }
    let target_preserved_paths = protected_paths_for_root(&profile, &install_root)?;
    let move_source_root = if let Some(registered_root) = registered_root {
        let registered_root = registered_root
            .canonicalize()
            .map_err(|error| format!("registered_install_root_unreadable: {error}"))?;
        if registered_root == install_root {
            None
        } else {
            if !move_intent {
                return Err("install_root_drift_requires_explicit_move".to_string());
            }
            if !root_contains_only_preserved_paths(&install_root, &target_preserved_paths)? {
                return Err("migration_target_must_be_new_and_empty".to_string());
            }
            if roots_overlap(&registered_root, &install_root) {
                return Err("migration_roots_must_not_overlap".to_string());
            }
            if !registered_root
                .join(format!("{main_binary_name}.exe"))
                .is_file()
            {
                return Err("registered_install_root_missing_main_binary".to_string());
            }
            Some(registered_root)
        }
    } else {
        None
    };
    let source_preserved_paths = move_source_root
        .as_deref()
        .map(|source| protected_paths_for_root(&profile, source))
        .transpose()?
        .unwrap_or_default();
    let mut terminated = Vec::new();
    if let Some(source_root) = &move_source_root {
        terminated.extend(quiesce_blockers(
            source_root,
            &main_binary_name,
            &profile.profile_id,
        )?);
    }
    terminated.extend(quiesce_blockers(
        &install_root,
        &main_binary_name,
        &profile.profile_id,
    )?);
    terminated.sort_unstable();
    terminated.dedup();
    begin_transaction(
        &install_root,
        &transaction_id,
        &main_binary_name,
        &registry_product_name,
        &manufacturer,
        &autostart_value_name,
        &shortcut_paths,
        move_source_root.clone(),
        source_preserved_paths,
        target_preserved_paths,
        target_existed_before_install,
    )?;
    Ok(json!({
        "ok": true,
        "recovered": recovered,
        "terminatedPids": terminated,
        "transition": if move_source_root.is_some() { "explicit-move" } else { "in-place" }
    }))
}

fn activate(args: &[String]) -> Result<serde_json::Value, String> {
    let install_root = PathBuf::from(required_arg(args, "--install-root")?)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let payload_relative_path = required_arg(args, "--payload-relative-path")?;
    let profile_id = required_arg(args, "--profile-id")?;
    let build_id = required_arg(args, "--build-id")?;
    let transaction_id = required_arg(args, "--transaction-id")?;
    let verified = install_contract::verify_candidate_payload(
        &install_root,
        &payload_relative_path,
        &profile_id,
        &build_id,
    )?;
    let receipt = ActiveInstallReceipt {
        schema_version: 1,
        profile_id,
        build_identity_sha256: verified.identity_sha256.clone(),
        build_id,
        payload_relative_path,
        payload_manifest_sha256: verified.manifest_sha256.clone(),
        activated_at: chrono::Utc::now().to_rfc3339(),
        transaction_id: transaction_id.clone(),
    };
    let temporary = install_root.join("active-install.json.pending");
    write_and_sync(
        &temporary,
        &serde_json::to_vec(&receipt).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    atomic_replace(
        &temporary,
        &install_root.join(install_contract::ACTIVE_INSTALL_FILE),
    )?;
    Ok(json!({
        "ok": true,
        "buildIdentitySha256": verified.identity_sha256,
        "payloadManifestSha256": verified.manifest_sha256
    }))
}

fn retire_source(args: &[String]) -> Result<serde_json::Value, String> {
    let install_root = PathBuf::from(required_arg(args, "--install-root")?)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let transaction_id = required_arg(args, "--transaction-id")?;
    let transaction = transaction_root(&install_root, &transaction_id)?;
    let mut state = read_state(&transaction)?;
    if state.committed {
        return Err("cannot retire source for committed transaction".to_string());
    }
    let source = state
        .move_source_root
        .as_ref()
        .ok_or("transaction is not an install-root migration")?;
    let source_parent = source.parent().ok_or("migration source has no parent")?;
    let source_name = source
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or("migration source has an invalid name")?;
    let backup = source_parent.join(format!(
        ".{source_name}.agent-friend-migration-{transaction_id}"
    ));
    if backup.exists() {
        return Err("migration source backup already exists".to_string());
    }
    state.move_source_backup = Some(backup.clone());
    write_state(&transaction, &state)?;
    fs::rename(source, &backup)
        .map_err(|error| format!("migration source retirement failed: {error}"))?;
    move_preserved_paths_from_backup(source, &backup, &state.source_preserved_paths)?;
    Ok(json!({"ok": true, "sourceRetired": true}))
}

fn commit(args: &[String]) -> Result<serde_json::Value, String> {
    let install_root = PathBuf::from(required_arg(args, "--install-root")?)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let transaction_id = required_arg(args, "--transaction-id")?;
    let profile_id = required_arg(args, "--profile-id")?;
    let build_id = required_arg(args, "--build-id")?;
    let active = install_contract::discover_active_payload(Some(&install_root))?
        .ok_or("installer commit requires active packaged payload")?;
    if active.receipt.profile_id != profile_id || active.receipt.build_id != build_id {
        return Err("installer_commit_identity_mismatch".to_string());
    }
    let transaction = transaction_root(&install_root, &transaction_id)?;
    let mut state = read_state(&transaction)?;
    if state.move_source_root.is_some() && state.move_source_backup.is_none() {
        return Err("migration source was not retired before commit".to_string());
    }
    state.committed = true;
    write_state(&transaction, &state)?;
    let mut pending = Vec::new();
    if let Some(backup) = &state.move_source_backup {
        if backup.exists() && fs::remove_dir_all(backup).is_err() {
            pending.push("retired-program-root".to_string());
        }
    }
    if pending.is_empty() {
        fs::remove_dir_all(&transaction).map_err(|error| error.to_string())?;
    }
    Ok(json!({"ok": true, "pending": pending}))
}

fn verify(args: &[String]) -> Result<serde_json::Value, String> {
    let install_root = PathBuf::from(required_arg(args, "--install-root")?)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let payload_relative_path = required_arg(args, "--payload-relative-path")?;
    let profile_id = required_arg(args, "--profile-id")?;
    let build_id = required_arg(args, "--build-id")?;
    let verified = install_contract::verify_candidate_payload(
        &install_root,
        &payload_relative_path,
        &profile_id,
        &build_id,
    )?;
    Ok(json!({
        "ok": true,
        "buildIdentitySha256": verified.identity_sha256,
        "payloadManifestSha256": verified.manifest_sha256
    }))
}

fn rollback(args: &[String]) -> Result<serde_json::Value, String> {
    let install_root = PathBuf::from(required_arg(args, "--install-root")?)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let transaction_id = required_arg(args, "--transaction-id")?;
    let transaction = transaction_root(&install_root, &transaction_id)?;
    rollback_transaction(&install_root, &transaction)?;
    Ok(json!({"ok": true, "rolledBack": transaction_id}))
}

fn cleanup(args: &[String]) -> Result<serde_json::Value, String> {
    let install_root = PathBuf::from(required_arg(args, "--install-root")?)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let profile_id = required_arg(args, "--profile-id")?;
    let build_id = required_arg(args, "--build-id")?;
    let profile = install_contract::embedded_profile()?;
    let identity = install_contract::embedded_identity()?
        .map(|(identity, _)| identity)
        .ok_or("installer cleanup requires embedded build identity")?;
    if profile_id != profile.profile_id
        || build_id != identity.build_id
        || identity.profile_id != profile.profile_id
    {
        return Err("installer_cleanup_identity_mismatch".to_string());
    }
    let active = install_contract::discover_active_payload(Some(&install_root))?
        .ok_or("installer cleanup requires active packaged payload")?;
    let mut cleaned = Vec::new();
    let mut pending = Vec::new();

    let legacy = install_root.join("runtime");
    if legacy.is_dir() {
        match fs::remove_dir_all(&legacy) {
            Ok(()) => cleaned.push("runtime".to_string()),
            Err(_) => pending.push("runtime".to_string()),
        }
    }

    let packages = install_root.join("packages");
    if packages.is_dir() {
        let packages = packages
            .canonicalize()
            .map_err(|error| format!("installer_cleanup_packages_unreadable: {error}"))?;
        for entry in fs::read_dir(&packages).map_err(|error| error.to_string())? {
            let path = entry.map_err(|error| error.to_string())?.path();
            if !path.is_dir() {
                continue;
            }
            let canonical = match path.canonicalize() {
                Ok(path) if path.starts_with(&packages) => path,
                _ => {
                    pending.push("packages/<unsafe-entry>".to_string());
                    continue;
                }
            };
            if canonical == active.payload_root {
                continue;
            }
            let candidate_identity = fs::read(canonical.join("build-identity.json"))
                .ok()
                .and_then(|bytes| {
                    serde_json::from_slice::<install_contract::BuildIdentity>(&bytes).ok()
                });
            let Some(candidate_identity) = candidate_identity else {
                pending.push("packages/<unidentified>".to_string());
                continue;
            };
            if candidate_identity.profile_id != profile.profile_id
                || validate_token(&candidate_identity.build_id, "inactive build id").is_err()
                || canonical.file_name().and_then(|name| name.to_str())
                    != Some(candidate_identity.build_id.as_str())
            {
                pending.push("packages/<identity-mismatch>".to_string());
                continue;
            }
            let relative = format!("packages/{}", candidate_identity.build_id);
            match fs::remove_dir_all(&canonical) {
                Ok(()) => cleaned.push(relative),
                Err(_) => pending.push(relative),
            }
        }
    }

    Ok(json!({"ok": true, "cleaned": cleaned, "pending": pending}))
}

fn run() -> Result<serde_json::Value, String> {
    let args: Vec<String> = env::args().skip(1).collect();
    let command = args.first().map(String::as_str).ok_or("missing command")?;
    match command {
        "preinstall" => preinstall(&args[1..]),
        "verify" => verify(&args[1..]),
        "activate" => activate(&args[1..]),
        "retire-source" => retire_source(&args[1..]),
        "commit" => commit(&args[1..]),
        "rollback" => rollback(&args[1..]),
        "cleanup" => cleanup(&args[1..]),
        _ => Err(format!("unknown command: {command}")),
    }
}

fn failure_payload(command: &str, error: &str) -> serde_json::Value {
    json!({
        "schemaVersion": 1,
        "ok": false,
        "command": command,
        "error": error,
    })
}

fn main() {
    let command = env::args()
        .nth(1)
        .unwrap_or_else(|| "<missing>".to_string());
    match run() {
        Ok(value) => println!("{value}"),
        Err(error) => {
            // The NSIS installer captures stdout into its pre-application
            // diagnostics file. Keep failures structured and on the same
            // channel as successful helper results.
            let mut stdout = std::io::stdout().lock();
            let _ = writeln!(stdout, "{}", failure_payload(&command, &error));
            let _ = stdout.flush();
            std::process::exit(32);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "agent-friend-installer-helper-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock should follow the Unix epoch")
                .as_nanos()
        ))
    }

    #[test]
    fn registry_components_reject_subkey_escape() {
        assert!(validate_registry_component("agent-friend", "product").is_ok());
        assert!(validate_registry_component("../other", "product").is_err());
        assert!(validate_registry_component("other\\product", "product").is_err());
        assert!(validate_registry_component("", "product").is_err());
    }

    #[test]
    fn helper_failures_use_the_installer_diagnostics_protocol() {
        assert_eq!(
            failure_payload("preinstall", "blocked"),
            json!({
                "schemaVersion": 1,
                "ok": false,
                "command": "preinstall",
                "error": "blocked",
            })
        );
    }

    #[test]
    fn transaction_state_accepts_pre_registry_schema_for_recovery() {
        let state: TransactionState = serde_json::from_value(json!({
            "schemaVersion": 1,
            "transactionId": "build-1",
            "mainBinaryName": "agent-friend",
            "mainExisted": true,
            "receiptExisted": true,
            "committed": false
        }))
        .expect("legacy transaction state should remain recoverable");

        assert!(state.registry_snapshot.is_none());
        assert!(state.source_preserved_paths.is_empty());
        assert!(state.target_preserved_paths.is_empty());
    }

    #[test]
    fn source_retirement_keeps_logs_in_place_and_can_roll_back() {
        let root = test_root("preserve-source-logs");
        let source = root.join("source");
        let backup = root.join("source-backup");
        let logs = source.join("Logs");
        fs::create_dir_all(&logs).unwrap();
        fs::write(source.join("agent-friend.exe"), b"old-shell").unwrap();
        fs::write(logs.join("sentinel.log"), b"keep").unwrap();

        fs::rename(&source, &backup).unwrap();
        move_preserved_paths_from_backup(&source, &backup, &[PathBuf::from("Logs")]).unwrap();

        assert!(source.join("Logs/sentinel.log").is_file());
        assert!(!source.join("agent-friend.exe").exists());
        assert!(backup.join("agent-friend.exe").is_file());
        assert!(!backup.join("Logs").exists());

        restore_preserved_paths_to_backup(&source, &backup, &[PathBuf::from("Logs")]).unwrap();
        fs::rename(&backup, &source).unwrap();
        assert!(source.join("Logs/sentinel.log").is_file());
        assert!(source.join("agent-friend.exe").is_file());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn migration_target_rollback_removes_program_but_preserves_logs() {
        let root = test_root("preserve-target-logs");
        fs::create_dir_all(root.join("Logs")).unwrap();
        fs::create_dir_all(root.join("packages/build-1")).unwrap();
        fs::write(root.join("Logs/sentinel.log"), b"keep").unwrap();
        fs::write(root.join("agent-friend.exe"), b"candidate-shell").unwrap();
        fs::write(
            root.join("packages/build-1/runtime.exe"),
            b"candidate-runtime",
        )
        .unwrap();

        remove_program_root_preserving(&root, &[PathBuf::from("Logs")]).unwrap();

        assert!(root.join("Logs/sentinel.log").is_file());
        assert!(!root.join("agent-friend.exe").exists());
        assert!(!root.join("packages").exists());

        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_extended_and_dos_paths_are_equivalent() {
        assert!(paths_equal(
            Path::new(r"\\?\C:\Users\Example\AppData\Local\agent-friend"),
            Path::new(r"c:\users\example\appdata\local\agent-friend\")
        ));
        assert!(paths_equal(
            Path::new(r"\\?\UNC\server\share\agent-friend"),
            Path::new(r"\\SERVER\SHARE\agent-friend")
        ));
    }
}
