use super::{
    archive::{ArchivePayload, ArchiveSource},
    model::AssetGroup,
};
use crate::paths;
use std::{fs, path::Path};

pub fn collect_file_assets() -> Result<Vec<ArchiveSource>, String> {
    let root = paths::user_data_dir();
    collect_file_assets_from(&root)
}

fn collect_file_assets_from(root: &Path) -> Result<Vec<ArchiveSource>, String> {
    let mut sources = Vec::new();
    collect_tree(
        &root.join("sessions"),
        "assets/sessions",
        AssetGroup::Sessions,
        &mut sources,
        &|path| is_temporary(path),
    )?;
    for name in ["blobs", "metadata"] {
        collect_tree(
            &root.join("attachments").join(name),
            &format!("assets/attachments/{name}"),
            AssetGroup::Attachments,
            &mut sources,
            &|path| is_temporary(path),
        )?;
    }
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

    #[test]
    fn attachment_inventory_includes_only_canonical_assets() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-migration-attachments-{}",
            rand::random::<u64>()
        ));
        for (relative, bytes) in [
            ("attachments/blobs/aa/asset", b"blob".as_slice()),
            ("attachments/metadata/asset.json", b"{}".as_slice()),
            ("attachments/drafts/draft.json", b"private".as_slice()),
            (
                "attachments/provider-cache/cache.json",
                b"derived".as_slice(),
            ),
            ("attachments/tmp/upload.part", b"temporary".as_slice()),
        ] {
            let path = root.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, bytes).unwrap();
        }

        let sources = collect_file_assets_from(&root).unwrap();
        let attachment_paths = sources
            .iter()
            .filter(|source| source.group == AssetGroup::Attachments)
            .map(|source| source.archive_path.as_str())
            .collect::<Vec<_>>();

        assert_eq!(
            attachment_paths,
            [
                "assets/attachments/blobs/aa/asset",
                "assets/attachments/metadata/asset.json"
            ]
        );
        fs::remove_dir_all(root).unwrap();
    }
}
