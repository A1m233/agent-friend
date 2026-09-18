//! Stream a frozen log prefix through redaction into an owned temporary file.
use std::{
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};

struct TemporaryPath(PathBuf);

impl Drop for TemporaryPath {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

pub(super) struct StagedLog {
    // Fields drop in order: close the file before removing its path on Windows.
    pub file: File,
    _path: TemporaryPath,
    pub original_bytes: u64,
    pub included_bytes: usize,
}

impl StagedLog {
    pub fn collect(
        path: &Path,
        credentials: &[String],
        home: Option<&Path>,
    ) -> Result<Self, String> {
        let input = File::open(path).map_err(|e| e.to_string())?;
        let original_bytes = input.metadata().map_err(|e| e.to_string())?.len();
        let temporary = std::env::temp_dir().join(format!(
            "agent-friend-diagnostic-log-{}-{}.tmp",
            std::process::id(),
            rand::random::<u128>()
        ));
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|e| e.to_string())?;
        let mut staged = Self {
            file,
            _path: TemporaryPath(temporary),
            original_bytes,
            included_bytes: 0,
        };
        // Also protect multiline credentials when processing one log line at a time.
        let credentials = credentials
            .iter()
            .flat_map(|value| value.lines())
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let mut reader = BufReader::new(input.take(original_bytes));
        let mut raw = Vec::new();
        let mut read_bytes = 0_u64;
        let mut first = true;
        loop {
            raw.clear();
            let count = reader
                .read_until(b'\n', &mut raw)
                .map_err(|e| e.to_string())?;
            if count == 0 {
                break;
            }
            read_bytes += count as u64;
            let (text, _) = super::redact_text(&String::from_utf8_lossy(&raw), &credentials, home);
            if !first {
                staged.file.write_all(b"\n").map_err(|e| e.to_string())?;
                staged.included_bytes += 1;
            }
            first = false;
            staged
                .file
                .write_all(text.as_bytes())
                .map_err(|e| e.to_string())?;
            staged.included_bytes += text.len();
        }
        if read_bytes != original_bytes {
            return Err("log snapshot was truncated during export".into());
        }
        staged
            .file
            .seek(SeekFrom::Start(0))
            .map_err(|e| e.to_string())?;
        Ok(staged)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_log_exceeds_old_limit_and_staging_is_cleaned_up() {
        let path = std::env::temp_dir().join(format!("af-log-test-{}", rand::random::<u128>()));
        let mut input = File::create(&path).unwrap();
        input.write_all(b"FIRST\nAuthorization: secret\n").unwrap();
        for _ in 0..6000 {
            input.write_all(&[b'x'; 1024]).unwrap();
            input.write_all(b"\n").unwrap();
        }
        input.write_all(b"LAST private-value").unwrap();
        drop(input);
        let mut staged = StagedLog::collect(&path, &["private-value".into()], None).unwrap();
        let temporary = staged._path.0.clone();
        let mut text = String::new();
        staged.file.read_to_string(&mut text).unwrap();
        assert!(staged.original_bytes > 5 * 1024 * 1024);
        assert!(text.starts_with("FIRST\n<REDACTED:CREDENTIAL-LINE>\n"));
        assert!(text.ends_with("LAST <REDACTED:CREDENTIAL>"));
        assert_eq!(text.len(), staged.included_bytes);
        assert!(!text.contains("secret"));
        drop(staged);
        assert!(!temporary.exists());
        fs::remove_file(path).unwrap();
    }
}
