use super::{
    archive::{ArchivePayload, ArchiveSource},
    model::AssetGroup,
};
use crate::paths;
use std::{fs, path::Path};

pub fn collect_file_assets() -> Result<Vec<ArchiveSource>, String> {
    let root = paths::user_data_dir();
    let mut sources = Vec::new();
    collect_tree(
        &root.join("sessions"),
        "assets/sessions",
        AssetGroup::Sessions,
        &mut sources,
        &|path| is_temporary(path),
    )?;
    collect_tree(
        &root.join("memory"),
        "assets/memory",
        AssetGroup::Memory,
        &mut sources,
        &|path| {
            let name = path
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("");
            name.starts_with("memory-vectors-") || is_temporary(path)
        },
    )?;
    collect_tree(
        &root.join("personas"),
        "assets/personas",
        AssetGroup::Persona,
        &mut sources,
        &|path| is_temporary(path),
    )?;
    collect_tree(
        &root.join("avatar-models"),
        "assets/avatar-models",
        AssetGroup::UserAvatarModels,
        &mut sources,
        &|path| {
            path.strip_prefix(root.join("avatar-models"))
                .ok()
                .and_then(|relative| relative.components().next())
                .is_some_and(|component| component.as_os_str() == "builtin")
                || is_temporary(path)
        },
    )?;
    let cli_history = root.join(".cli_history");
    if cli_history.is_file() {
        sources.push(ArchiveSource {
            source: ArchivePayload::File(cli_history),
            archive_path: "assets/.cli_history".to_string(),
            group: AssetGroup::CliHistory,
        });
    }
    sources.sort_by(|left, right| left.archive_path.cmp(&right.archive_path));
    Ok(sources)
}

fn collect_tree(
    root: &Path,
    prefix: &str,
    group: AssetGroup,
    output: &mut Vec<ArchiveSource>,
    excluded: &impl Fn(&Path) -> bool,
) -> Result<(), String> {
    if !root.exists() {
        return Ok(());
    }
    let canonical_root = root
        .canonicalize()
        .map_err(|error| format!("migration_asset_root_invalid: {error}"))?;
    collect_directory(root, &canonical_root, prefix, group, output, excluded)
}

fn collect_directory(
    directory: &Path,
    canonical_root: &Path,
    prefix: &str,
    group: AssetGroup,
    output: &mut Vec<ArchiveSource>,
    excluded: &impl Fn(&Path) -> bool,
) -> Result<(), String> {
    let mut entries = fs::read_dir(directory)
        .map_err(|error| format!("migration_asset_read_failed: {error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("migration_asset_read_failed: {error}"))?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        if excluded(&path) {
            continue;
        }
        let metadata = fs::symlink_metadata(&path)
            .map_err(|error| format!("migration_asset_metadata_failed: {error}"))?;
        if metadata.file_type().is_symlink() {
            return Err("migration_asset_link_rejected".to_string());
        }
        if metadata.is_dir() {
            collect_directory(&path, canonical_root, prefix, group, output, excluded)?;
        } else if metadata.is_file() {
            let canonical = path
                .canonicalize()
                .map_err(|error| format!("migration_asset_path_invalid: {error}"))?;
            if !canonical.starts_with(canonical_root) {
                return Err("migration_asset_escape_rejected".to_string());
            }
            let relative = canonical
                .strip_prefix(canonical_root)
                .map_err(|_| "migration_asset_escape_rejected".to_string())?;
            let relative = relative
                .components()
                .map(|component| component.as_os_str().to_string_lossy())
                .collect::<Vec<_>>()
                .join("/");
            output.push(ArchiveSource {
                source: ArchivePayload::File(canonical),
                archive_path: format!("{prefix}/{relative}"),
                group,
            });
        }
    }
    Ok(())
}

fn is_temporary(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("");
    name.ends_with(".lock") || name.ends_with(".tmp") || name.ends_with(".part")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn temporary_file_detection_is_narrow() {
        assert!(is_temporary(Path::new("metadata.json.lock")));
        assert!(is_temporary(Path::new("write.tmp")));
        assert!(!is_temporary(Path::new("session.jsonl")));
    }
}
