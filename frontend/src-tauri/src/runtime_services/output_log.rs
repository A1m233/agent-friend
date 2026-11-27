//! The parent owns rotation; children only own pipe handles.
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    thread::{self, JoinHandle},
    time::{SystemTime, UNIX_EPOCH},
};

const FILE_BYTES: u64 = 10 * 1024 * 1024;
const BACKUPS: usize = 5;
const LEGACY_SECONDS: u64 = 7 * 24 * 60 * 60;

pub(super) struct OutputLog {
    file: Option<File>,
    path: PathBuf,
    bytes: u64,
    limit: u64,
    _lock: File,
    failed: bool,
    dropped: u64,
}

impl OutputLog {
    pub fn open(path: &Path) -> io::Result<Self> {
        Self::with_limit(path, FILE_BYTES)
    }
    fn with_limit(path: &Path, limit: u64) -> io::Result<Self> {
        let parent = path
            .parent()
            .ok_or_else(|| io::Error::other("missing log directory"))?;
        fs::create_dir_all(parent)?;
        let lock_path = path.with_extension("log.writer-lock");
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(lock_path)?;
        if !fs4::fs_std::FileExt::try_lock_exclusive(&lock)? {
            return Err(io::Error::other("runtime log writer is still active"));
        }
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        reap_legacy(path, now)?;
        if let Ok(meta) = fs::symlink_metadata(path) {
            if !meta.is_file() || meta.file_type().is_symlink() {
                return Err(io::Error::other("runtime log is not a regular file"));
            }
            if meta.len() > limit {
                let name = path.file_name().unwrap().to_string_lossy();
                fs::rename(
                    path,
                    parent.join(format!(
                        "{name}.legacy-{now}-{:032x}",
                        rand::random::<u128>()
                    )),
                )?;
            }
        }
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        let bytes = file.metadata()?.len();
        Ok(Self {
            file: Some(file),
            path: path.into(),
            bytes,
            limit,
            _lock: lock,
            failed: false,
            dropped: 0,
        })
    }

    fn rotate(&mut self) -> io::Result<()> {
        self.file.take(); // Close before rename on Windows.
        let name = self.path.file_name().unwrap().to_string_lossy();
        let parent = self.path.parent().unwrap();
        for index in (1..=BACKUPS).rev() {
            let destination = parent.join(format!("{name}.{index}"));
            if index == BACKUPS && destination.exists() {
                fs::remove_file(&destination)?;
            }
            let source = if index == 1 {
                self.path.clone()
            } else {
                parent.join(format!("{name}.{}", index - 1))
            };
            if source.exists() {
                fs::rename(source, destination)?;
            }
        }
        self.file = Some(
            OpenOptions::new()
                .create_new(true)
                .append(true)
                .open(&self.path)?,
        );
        self.bytes = 0;
        Ok(())
    }

    fn write_chunk(&mut self, mut bytes: &[u8]) -> io::Result<()> {
        while !bytes.is_empty() {
            if self.bytes >= self.limit {
                self.rotate()?;
            }
            let count = bytes.len().min((self.limit - self.bytes) as usize);
            self.file
                .as_mut()
                .ok_or_else(|| io::Error::other("log writer closed"))?
                .write_all(&bytes[..count])?;
            self.bytes += count as u64;
            bytes = &bytes[count..];
        }
        Ok(())
    }

    pub fn drain(
        output: &Arc<Mutex<Self>>,
        mut input: impl Read + Send + 'static,
    ) -> JoinHandle<()> {
        let output = output.clone();
        thread::spawn(move || {
            let mut buffer = [0_u8; 16 * 1024];
            loop {
                let count = match input.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(n) => n,
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    Err(error) => {
                        log::warn!("runtime log pipe read failed: {error}");
                        break;
                    }
                };
                let mut output = output.lock().unwrap();
                if output.failed {
                    output.dropped += count as u64;
                    continue; // Keep draining pipes even if the disk is no longer writable.
                }
                if let Err(error) = output.write_chunk(&buffer[..count]) {
                    output.failed = true;
                    output.dropped += count as u64;
                    log::error!(
                        "runtime output log failed; subsequent output will be discarded: {error}"
                    );
                }
            }
        })
    }
}

impl Drop for OutputLog {
    fn drop(&mut self) {
        if self.dropped > 0 {
            log::error!(
                "runtime output logging lost at least {} bytes",
                self.dropped
            );
        }
    }
}

fn reap_legacy(path: &Path, now: u64) -> io::Result<()> {
    let prefix = format!("{}.legacy-", path.file_name().unwrap().to_string_lossy());
    for entry in fs::read_dir(path.parent().unwrap())? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        let Some((stamp, nonce)) = name
            .strip_prefix(&prefix)
            .and_then(|name| name.split_once('-'))
        else {
            continue;
        };
        let Ok(stamp) = stamp.parse::<u64>() else {
            continue;
        };
        if nonce.len() == 32
            && nonce.bytes().all(|b| b.is_ascii_hexdigit())
            && now.saturating_sub(stamp) >= LEGACY_SECONDS
            && entry.file_type()?.is_file()
        {
            fs::remove_file(entry.path())?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rotation_bounds_long_unterminated_output_and_preserves_order() {
        let root =
            std::env::temp_dir().join(format!("af-rotation-{:032x}", rand::random::<u128>()));
        let path = root.join("runtime-bridge.log");
        let mut writer = OutputLog::with_limit(&path, 10).unwrap();
        writer.write_chunk(&(0..87_u8).collect::<Vec<_>>()).unwrap();
        drop(writer);
        let mut retained = Vec::new();
        for index in (1..=BACKUPS).rev() {
            retained.extend(fs::read(root.join(format!("runtime-bridge.log.{index}"))).unwrap());
        }
        retained.extend(fs::read(&path).unwrap());
        assert_eq!(retained, (30..87_u8).collect::<Vec<_>>());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn legacy_is_not_scanned_and_expires_seven_days_after_migration() {
        let root = std::env::temp_dir().join(format!("af-legacy-{:032x}", rand::random::<u128>()));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("runtime-bridge.log");
        fs::write(&path, b"old giant content").unwrap();
        let writer = OutputLog::with_limit(&path, 10).unwrap();
        let legacy = fs::read_dir(&root)
            .unwrap()
            .flatten()
            .find(|entry| entry.file_name().to_string_lossy().contains(".legacy-"))
            .unwrap()
            .path();
        assert_eq!(fs::read(&legacy).unwrap(), b"old giant content");
        drop(writer);
        let stamp = legacy
            .file_name()
            .unwrap()
            .to_string_lossy()
            .split(".legacy-")
            .nth(1)
            .unwrap()
            .split('-')
            .next()
            .unwrap()
            .parse::<u64>()
            .unwrap();
        reap_legacy(&path, stamp + LEGACY_SECONDS - 1).unwrap();
        assert!(legacy.exists());
        reap_legacy(&path, stamp + LEGACY_SECONDS).unwrap();
        assert!(!legacy.exists());
        fs::remove_dir_all(root).unwrap();
    }
}
