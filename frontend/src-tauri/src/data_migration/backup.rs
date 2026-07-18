use super::{
    archive::{self, ArchivePayload, ArchiveSource},
    crypto, dpapi, inventory,
    model::AssetGroup,
};
use crate::{paths, settings::Settings, ui_state::ChatUiPersistence};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, fs};

const PACKAGE_NAME: &str = "backup.afrollback";
const KEY_NAME: &str = "key.dpapi";
const META_NAME: &str = "meta.json";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackupMetadata {
    pub created_at: String,
    pub source: String,
    pub size: u64,
}

pub fn create_latest(
    settings: &Settings,
    ui_continuity: &ChatUiPersistence,
    runtime_secrets: &BTreeMap<String, String>,
    im_credentials: &serde_json::Value,
    source: &str,
) -> Result<BackupMetadata, String> {
    let root = paths::migration_backups_dir();
    fs::create_dir_all(&root).map_err(|error| format!("backup_root_create_failed: {error}"))?;
    let nonce = format!("{:016x}", rand::random::<u64>());
    let pending = root.join(format!(".pending-{nonce}"));
    let previous = root.join(format!(".previous-{nonce}"));
    let latest = root.join("latest");
    fs::create_dir_all(&pending)
        .map_err(|error| format!("backup_pending_create_failed: {error}"))?;

    let result = (|| {
        let code = crypto::generate_transfer_code()?;
        let mut sources = inventory::collect_file_assets()?;
        sources.push(json_source(
            "state/settings.json",
            AssetGroup::Settings,
            settings,
        )?);
        sources.push(json_source(
            "state/ui-continuity.json",
            AssetGroup::UiContinuity,
            ui_continuity,
        )?);
        sources.push(json_source(
            "secrets/runtime.json",
            AssetGroup::RuntimeCredentials,
            runtime_secrets,
        )?);
        sources.push(json_source(
            "secrets/im.json",
            AssetGroup::ImCredentials,
            im_credentials,
        )?);
        let package = pending.join(PACKAGE_NAME);
        crypto::encrypt_with(&package, &code, |writer| {
            archive::create_archive(writer, &sources)?;
            Ok(())
        })?;
        let protected = dpapi::protect_current_user(code.as_bytes())?;
        fs::write(pending.join(KEY_NAME), protected)
            .map_err(|error| format!("backup_key_write_failed: {error}"))?;
        verify_directory(&pending)?;
        let metadata = BackupMetadata {
            created_at: chrono::Utc::now().to_rfc3339(),
            source: source.to_string(),
            size: package
                .metadata()
                .map_err(|error| format!("backup_metadata_failed: {error}"))?
                .len(),
        };
        fs::write(
            pending.join(META_NAME),
            serde_json::to_vec_pretty(&metadata)
                .map_err(|error| format!("backup_metadata_serialize_failed: {error}"))?,
        )
        .map_err(|error| format!("backup_metadata_write_failed: {error}"))?;

        if latest.exists() {
            fs::rename(&latest, &previous)
                .map_err(|error| format!("backup_rotation_prepare_failed: {error}"))?;
        }
        if let Err(error) = fs::rename(&pending, &latest) {
            if previous.exists() && !latest.exists() {
                let _ = fs::rename(&previous, &latest);
            }
            return Err(format!("backup_rotation_commit_failed: {error}"));
        }
        let _ = fs::remove_dir_all(&previous);
        Ok(metadata)
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&pending);
    }
    result
}

pub fn latest_metadata() -> Result<Option<BackupMetadata>, String> {
    let path = paths::migration_backups_dir()
        .join("latest")
        .join(META_NAME);
    if !path.exists() {
        return Ok(None);
    }
    let bytes = fs::read(path).map_err(|error| format!("backup_metadata_read_failed: {error}"))?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|error| format!("backup_metadata_invalid: {error}"))
}

pub fn extract_latest(destination: &std::path::Path) -> Result<archive::ExtractedArchive, String> {
    let latest = paths::migration_backups_dir().join("latest");
    let protected = fs::read(latest.join(KEY_NAME))
        .map_err(|error| format!("backup_key_read_failed: {error}"))?;
    let code = String::from_utf8(dpapi::unprotect_current_user(&protected)?)
        .map_err(|_| "backup_key_invalid".to_string())?;
    let mut manifest = None;
    crypto::decrypt_with(&latest.join(PACKAGE_NAME), &code, |reader| {
        manifest = Some(archive::extract_migration_archive(reader, destination)?);
        Ok(())
    })?;
    manifest.ok_or_else(|| "backup_manifest_missing".to_string())
}

pub fn delete_latest() -> Result<(), String> {
    let latest = paths::migration_backups_dir().join("latest");
    if latest.exists() {
        fs::remove_dir_all(latest).map_err(|error| format!("backup_delete_failed: {error}"))?;
    }
    Ok(())
}

fn verify_directory(directory: &std::path::Path) -> Result<(), String> {
    let protected = fs::read(directory.join(KEY_NAME))
        .map_err(|error| format!("backup_key_verify_failed: {error}"))?;
    let code = String::from_utf8(dpapi::unprotect_current_user(&protected)?)
        .map_err(|_| "backup_key_invalid".to_string())?;
    crypto::decrypt_with(&directory.join(PACKAGE_NAME), &code, |reader| {
        archive::verify_archive(reader)?;
        Ok(())
    })
}

fn json_source(
    archive_path: &str,
    group: AssetGroup,
    value: &impl Serialize,
) -> Result<ArchiveSource, String> {
    Ok(ArchiveSource {
        source: ArchivePayload::Bytes(
            serde_json::to_vec(value)
                .map_err(|error| format!("backup_payload_serialize_failed: {error}"))?,
        ),
        archive_path: archive_path.to_string(),
        group,
    })
}
