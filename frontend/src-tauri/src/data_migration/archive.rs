use super::{
    disk,
    model::{AssetGroup, Manifest, ManifestEntry, MANIFEST_VERSION},
};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};
use tar::{Archive, Builder, EntryType, Header};

const MANIFEST_PATH: &str = "manifest.json";
const MAX_ENTRY_COUNT: usize = 100_000;
const MAX_TOTAL_BYTES: u64 = 100 * 1024 * 1024 * 1024;
const MAX_SECRET_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct ArchiveSource {
    pub source: ArchivePayload,
    pub archive_path: String,
    pub group: AssetGroup,
}

#[derive(Clone, Debug)]
pub enum ArchivePayload {
    File(PathBuf),
    Bytes(Vec<u8>),
}

pub struct ExtractedArchive {
    pub manifest: Manifest,
    pub secrets: HashMap<String, Vec<u8>>,
}

pub fn create_archive(output: impl Write, sources: &[ArchiveSource]) -> Result<Manifest, String> {
    let mut manifest_entries = Vec::with_capacity(sources.len());
    let mut seen = HashSet::new();
    for source in sources {
        validate_archive_path(&source.archive_path)?;
        if !seen.insert(source.archive_path.to_ascii_lowercase()) {
            return Err("archive_duplicate_path".to_string());
        }
        let (size, sha256) = source_hash(&source.source)?;
        manifest_entries.push(ManifestEntry {
            path: source.archive_path.clone(),
            group: source.group,
            size,
            sha256,
        });
    }
    let manifest = Manifest::new(manifest_entries);
    let manifest_bytes = serde_json::to_vec(&manifest)
        .map_err(|error| format!("archive_manifest_write_failed: {error}"))?;
    let mut archive = Builder::new(output);
    append_bytes(&mut archive, MANIFEST_PATH, &manifest_bytes)?;
    for source in sources {
        let size = source_size(&source.source)?;
        let mut header = regular_header(size);
        match &source.source {
            ArchivePayload::File(path) => {
                let mut input = File::open(path)
                    .map_err(|error| format!("archive_source_open_failed: {error}"))?;
                archive
                    .append_data(&mut header, &source.archive_path, &mut input)
                    .map_err(|error| format!("archive_entry_write_failed: {error}"))?;
            }
            ArchivePayload::Bytes(bytes) => archive
                .append_data(&mut header, &source.archive_path, bytes.as_slice())
                .map_err(|error| format!("archive_entry_write_failed: {error}"))?,
        }
    }
    archive
        .finish()
        .map_err(|error| format!("archive_finish_failed: {error}"))?;
    Ok(manifest)
}

#[cfg(test)]
pub fn extract_archive(input: impl Read, destination: &Path) -> Result<Manifest, String> {
    extract_archive_internal(input, destination, false).map(|result| result.manifest)
}

pub fn extract_migration_archive(
    input: impl Read,
    destination: &Path,
) -> Result<ExtractedArchive, String> {
    extract_archive_internal(input, destination, true)
}

fn extract_archive_internal(
    input: impl Read,
    destination: &Path,
    secrets_in_memory: bool,
) -> Result<ExtractedArchive, String> {
    fs::create_dir_all(destination)
        .map_err(|error| format!("archive_staging_create_failed: {error}"))?;
    let mut archive = Archive::new(input);
    let mut manifest: Option<Manifest> = None;
    let mut staged = HashMap::new();
    let mut seen = HashSet::new();
    let mut secrets = HashMap::new();
    let mut total = 0_u64;

    for (index, next) in archive
        .entries()
        .map_err(|error| format!("archive_invalid: {error}"))?
        .enumerate()
    {
        if index >= MAX_ENTRY_COUNT {
            return Err("archive_entry_limit_exceeded".to_string());
        }
        let mut entry = next.map_err(|error| format!("archive_entry_invalid: {error}"))?;
        if entry.header().entry_type() != EntryType::Regular {
            return Err("archive_special_entry_rejected".to_string());
        }
        let archive_path = entry
            .path()
            .map_err(|error| format!("archive_path_invalid: {error}"))?
            .to_str()
            .ok_or_else(|| "archive_path_non_unicode".to_string())?
            .replace('\\', "/");
        validate_archive_path(&archive_path)?;
        if !seen.insert(archive_path.to_ascii_lowercase()) {
            return Err("archive_duplicate_path".to_string());
        }
        if archive_path == MANIFEST_PATH {
            if manifest.is_some() || entry.size() > 4 * 1024 * 1024 {
                return Err("archive_manifest_invalid".to_string());
            }
            let parsed: Manifest = serde_json::from_reader(&mut entry)
                .map_err(|error| format!("archive_manifest_invalid: {error}"))?;
            if parsed.schema_version != MANIFEST_VERSION {
                return Err("archive_manifest_version_unsupported".to_string());
            }
            manifest = Some(parsed);
            continue;
        }

        total = total
            .checked_add(entry.size())
            .ok_or_else(|| "archive_size_overflow".to_string())?;
        if total > MAX_TOTAL_BYTES {
            return Err("archive_total_size_exceeded".to_string());
        }
        if secrets_in_memory && is_secret_path(&archive_path) {
            if entry.size() > MAX_SECRET_BYTES {
                return Err("archive_secret_size_exceeded".to_string());
            }
            let mut bytes = Vec::with_capacity(entry.size() as usize);
            let (size, digest) = copy_with_hash(&mut entry, &mut bytes)?;
            staged.insert(archive_path.clone(), (size, digest));
            secrets.insert(archive_path, bytes);
            continue;
        }
        disk::ensure_available_space(destination, entry.size())?;
        let target = destination.join(path_from_archive(&archive_path)?);
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("archive_entry_parent_failed: {error}"))?;
        }
        let partial = target.with_extension("part");
        let mut output = File::create(&partial)
            .map_err(|error| format!("archive_entry_create_failed: {error}"))?;
        let (size, digest) = copy_with_hash(&mut entry, &mut output)?;
        output
            .sync_all()
            .map_err(|error| format!("archive_entry_sync_failed: {error}"))?;
        fs::rename(&partial, &target)
            .map_err(|error| format!("archive_entry_commit_failed: {error}"))?;
        staged.insert(archive_path, (size, digest));
    }

    let manifest = manifest.ok_or_else(|| "archive_manifest_missing".to_string())?;
    if manifest.entries.len() != staged.len() {
        return Err("archive_unlisted_entry".to_string());
    }
    let mut manifest_seen = HashSet::new();
    for expected in &manifest.entries {
        validate_archive_path(&expected.path)?;
        if !manifest_seen.insert(expected.path.to_ascii_lowercase()) {
            return Err("archive_duplicate_path".to_string());
        }
        match staged.get(&expected.path) {
            Some((size, digest)) if *size == expected.size && digest == &expected.sha256 => {}
            _ => return Err("archive_entry_integrity_failed".to_string()),
        }
    }
    Ok(ExtractedArchive { manifest, secrets })
}

fn is_secret_path(path: &str) -> bool {
    matches!(path, "secrets/runtime.json" | "secrets/im.json")
}

pub fn verify_archive(input: impl Read) -> Result<Manifest, String> {
    let mut archive = Archive::new(input);
    let mut manifest: Option<Manifest> = None;
    let mut verified = HashMap::new();
    let mut seen = HashSet::new();
    let mut total = 0_u64;
    for (index, next) in archive
        .entries()
        .map_err(|error| format!("archive_invalid: {error}"))?
        .enumerate()
    {
        if index >= MAX_ENTRY_COUNT {
            return Err("archive_entry_limit_exceeded".to_string());
        }
        let mut entry = next.map_err(|error| format!("archive_entry_invalid: {error}"))?;
        if entry.header().entry_type() != EntryType::Regular {
            return Err("archive_special_entry_rejected".to_string());
        }
        let archive_path = entry
            .path()
            .map_err(|error| format!("archive_path_invalid: {error}"))?
            .to_str()
            .ok_or_else(|| "archive_path_non_unicode".to_string())?
            .replace('\\', "/");
        validate_archive_path(&archive_path)?;
        if !seen.insert(archive_path.to_ascii_lowercase()) {
            return Err("archive_duplicate_path".to_string());
        }
        if archive_path == MANIFEST_PATH {
            if manifest.is_some() || entry.size() > 4 * 1024 * 1024 {
                return Err("archive_manifest_invalid".to_string());
            }
            let parsed: Manifest = serde_json::from_reader(&mut entry)
                .map_err(|error| format!("archive_manifest_invalid: {error}"))?;
            if parsed.schema_version != MANIFEST_VERSION {
                return Err("archive_manifest_version_unsupported".to_string());
            }
            manifest = Some(parsed);
            continue;
        }
        total = total
            .checked_add(entry.size())
            .ok_or_else(|| "archive_size_overflow".to_string())?;
        if total > MAX_TOTAL_BYTES {
            return Err("archive_total_size_exceeded".to_string());
        }
        verified.insert(
            archive_path,
            copy_with_hash(&mut entry, &mut std::io::sink())?,
        );
    }
    let manifest = manifest.ok_or_else(|| "archive_manifest_missing".to_string())?;
    if manifest.entries.len() != verified.len() {
        return Err("archive_unlisted_entry".to_string());
    }
    let mut manifest_seen = HashSet::new();
    for expected in &manifest.entries {
        validate_archive_path(&expected.path)?;
        if !manifest_seen.insert(expected.path.to_ascii_lowercase()) {
            return Err("archive_duplicate_path".to_string());
        }
        match verified.get(&expected.path) {
            Some((size, digest)) if *size == expected.size && digest == &expected.sha256 => {}
            _ => return Err("archive_entry_integrity_failed".to_string()),
        }
    }
    Ok(manifest)
}

fn append_bytes(archive: &mut Builder<impl Write>, path: &str, bytes: &[u8]) -> Result<(), String> {
    let mut header = regular_header(bytes.len() as u64);
    archive
        .append_data(&mut header, path, bytes)
        .map_err(|error| format!("archive_manifest_write_failed: {error}"))
}

fn regular_header(size: u64) -> Header {
    let mut header = Header::new_gnu();
    header.set_size(size);
    header.set_mode(0o600);
    header.set_mtime(0);
    header.set_entry_type(EntryType::Regular);
    header.set_cksum();
    header
}

fn source_hash(source: &ArchivePayload) -> Result<(u64, String), String> {
    match source {
        ArchivePayload::File(path) => {
            let mut input =
                File::open(path).map_err(|error| format!("archive_source_open_failed: {error}"))?;
            copy_with_hash(&mut input, &mut std::io::sink())
        }
        ArchivePayload::Bytes(bytes) => copy_with_hash(&mut bytes.as_slice(), &mut std::io::sink()),
    }
}

fn source_size(source: &ArchivePayload) -> Result<u64, String> {
    match source {
        ArchivePayload::File(path) => path
            .metadata()
            .map(|metadata| metadata.len())
            .map_err(|error| format!("archive_source_metadata_failed: {error}")),
        ArchivePayload::Bytes(bytes) => Ok(bytes.len() as u64),
    }
}

fn copy_with_hash(
    reader: &mut impl Read,
    writer: &mut impl Write,
) -> Result<(u64, String), String> {
    let mut hasher = Sha256::new();
    let mut size = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|error| format!("archive_read_failed: {error}"))?;
        if read == 0 {
            break;
        }
        writer
            .write_all(&buffer[..read])
            .map_err(|error| format!("archive_write_failed: {error}"))?;
        hasher.update(&buffer[..read]);
        size += read as u64;
    }
    Ok((size, format!("{:x}", hasher.finalize())))
}

fn validate_archive_path(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.contains('\\')
        || value.contains('\0')
        || value.starts_with('/')
        || value.contains(':')
        || Path::new(value).components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err("archive_path_rejected".to_string());
    }
    Ok(())
}

fn path_from_archive(value: &str) -> Result<PathBuf, String> {
    validate_archive_path(value)?;
    Ok(value.split('/').collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn archive_round_trip_verifies_manifest() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-migration-archive-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&root).unwrap();
        let source = root.join("session.jsonl");
        let extracted = root.join("extracted");
        fs::write(&source, b"event-one\nevent-two\n").unwrap();
        let mut bytes = Vec::new();
        let manifest = create_archive(
            &mut bytes,
            &[ArchiveSource {
                source: ArchivePayload::File(source),
                archive_path: "assets/sessions/example.jsonl".to_string(),
                group: AssetGroup::Sessions,
            }],
        )
        .unwrap();
        assert_eq!(verify_archive(bytes.as_slice()).unwrap(), manifest);
        let restored = extract_archive(bytes.as_slice(), &extracted).unwrap();
        assert_eq!(restored, manifest);
        assert_eq!(
            fs::read(extracted.join("assets/sessions/example.jsonl")).unwrap(),
            b"event-one\nevent-two\n"
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn traversal_and_windows_ambiguous_paths_are_rejected() {
        for path in ["../secret", "/absolute", "C:/windows", "a\\b", "a/../../b"] {
            assert!(validate_archive_path(path).is_err(), "{path}");
        }
    }

    #[test]
    fn migration_secrets_are_verified_without_being_written_to_staging() {
        let root = std::env::temp_dir().join(format!(
            "agent-friend-migration-secret-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&root).unwrap();
        let mut archive = Vec::new();
        create_archive(
            &mut archive,
            &[ArchiveSource {
                source: ArchivePayload::Bytes(br#"{"DEEPSEEK_API_KEY":"private"}"#.to_vec()),
                archive_path: "secrets/runtime.json".to_string(),
                group: AssetGroup::RuntimeCredentials,
            }],
        )
        .unwrap();

        let extracted = extract_migration_archive(archive.as_slice(), &root).unwrap();

        assert!(!root.join("secrets/runtime.json").exists());
        assert_eq!(
            extracted.secrets.get("secrets/runtime.json").unwrap(),
            br#"{"DEEPSEEK_API_KEY":"private"}"#
        );
        fs::remove_dir_all(root).unwrap();
    }
}
