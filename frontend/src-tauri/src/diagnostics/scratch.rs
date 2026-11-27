//! Owned, locked scratch files. Reaping never follows links or touches final ZIPs.
use std::{
    fs::{self, File, OpenOptions},
    io::{Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

pub(super) type CleanupWarnings = Arc<Mutex<Vec<String>>>;
struct RemoveOnDrop(PathBuf, Option<CleanupWarnings>);
impl Drop for RemoveOnDrop {
    fn drop(&mut self) {
        if let Err(error) = fs::remove_file(&self.0) {
            if error.kind() != std::io::ErrorKind::NotFound {
                log::warn!("diagnostic scratch cleanup failed: {error}");
                if let Some(warnings) = &self.1 {
                    if let Ok(mut warnings) = warnings.lock() {
                        warnings.push("诊断临时文件清理失败，将在下次导出时重试。".into());
                    }
                }
            }
        }
    }
}

pub(super) struct Scratch {
    pub file: File,
    path: RemoveOnDrop,
}

impl Scratch {
    pub fn track_cleanup(mut self, control: &super::job::Control) -> Self {
        self.path.1 = Some(control.cleanup_warnings());
        self
    }
    pub fn archive(output_dir: &Path) -> Result<Self, String> {
        fs::create_dir_all(output_dir).map_err(|e| e.to_string())?;
        let work = crate::paths::diagnostic_archive_work_dir(output_dir);
        reap(&work)?;
        Self::in_dir(&work)
    }
    pub fn new() -> Result<Self, String> {
        Self::in_dir(&crate::paths::diagnostic_work_dir())
    }
    pub fn in_dir(dir: &Path) -> Result<Self, String> {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        if fs::symlink_metadata(dir)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_symlink()
        {
            return Err("diagnostic scratch directory must not be a link".into());
        }
        ensure_space(dir, 0)?;
        let path = dir.join(format!("af-export-{:032x}.part", rand::random::<u128>()));
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| e.to_string())?;
        let owned = Self {
            file,
            path: RemoveOnDrop(path, None),
        };
        fs4::fs_std::FileExt::lock_exclusive(&owned.file).map_err(|e| e.to_string())?;
        Ok(owned)
    }
    pub fn rewind(&mut self) -> Result<(), String> {
        self.file.flush().map_err(|e| e.to_string())?;
        self.file
            .seek(SeekFrom::Start(0))
            .map_err(|e| e.to_string())?;
        Ok(())
    }
    pub fn path(&self) -> &Path {
        &self.path.0
    }
}

pub(super) fn ensure_space(dir: &Path, required: u64) -> Result<(), String> {
    let available = fs4::available_space(dir)
        .map_err(|e| format!("cannot check diagnostic disk space: {e}"))?;
    if available < required.saturating_add(16 * 1024 * 1024) {
        return Err("诊断导出可用磁盘空间不足，请释放空间后重试。".into());
    }
    Ok(())
}

pub(super) fn reap(dir: &Path) -> Result<(), String> {
    if !dir.exists() {
        return Ok(());
    }
    if fs::symlink_metadata(dir)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_symlink()
    {
        return Err("diagnostic scratch directory must not be a link".into());
    }
    for entry in fs::read_dir(dir).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry.file_name().to_string_lossy().to_string();
        let Some(token) = name
            .strip_prefix("af-export-")
            .and_then(|s| s.strip_suffix(".part"))
        else {
            continue;
        };
        if token.len() != 32 || !token.bytes().all(|b| b.is_ascii_hexdigit()) {
            continue;
        }
        if !entry.file_type().map_err(|e| e.to_string())?.is_file() {
            continue;
        }
        let file = match OpenOptions::new().read(true).write(true).open(entry.path()) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error.to_string()),
        };
        if fs4::fs_std::FileExt::try_lock_exclusive(&file).map_err(|e| e.to_string())? {
            drop(file);
            if let Err(error) = fs::remove_file(entry.path()) {
                if error.kind() != std::io::ErrorKind::NotFound {
                    return Err(error.to_string());
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reap_only_abandoned_owned_files() {
        let root =
            std::env::temp_dir().join(format!("af-scratch-test-{:032x}", rand::random::<u128>()));
        let owned = Scratch::in_dir(&root).unwrap();
        let path = owned.path().to_owned();
        let stale = root.join(format!("af-export-{}.part", "a".repeat(32)));
        fs::write(&stale, "interrupted").unwrap();
        let unrelated = root.join("my-file.part");
        fs::write(&unrelated, "keep").unwrap();
        reap(&root).unwrap();
        assert!(path.exists());
        assert!(!stale.exists());
        assert!(unrelated.exists());
        drop(owned);
        assert!(!path.exists());
        fs::remove_file(unrelated).unwrap();
        fs::remove_dir(root).unwrap();
    }
}
