//! Stream a frozen log prefix through redaction into an owned temporary file.
use std::{
    fs::File,
    io::{BufRead, BufReader, BufWriter, Read, Seek, SeekFrom, Write},
    path::Path,
};

pub(super) struct StagedLog {
    // Fields drop in order: close the file before removing its path on Windows.
    pub staged: super::scratch::Scratch,
    pub original_bytes: u64,
    pub included_bytes: usize,
    pub tail_start_offset: u64,
    pub read_bytes: u64,
}

impl StagedLog {
    #[cfg(test)]
    pub fn collect(
        path: &Path,
        credentials: &[String],
        home: Option<&Path>,
    ) -> Result<Self, String> {
        Self::collect_range(
            path,
            credentials,
            home,
            None,
            &super::job::Control::detached(),
        )
    }

    pub fn collect_range(
        path: &Path,
        credentials: &[String],
        home: Option<&Path>,
        max_bytes: Option<u64>,
        control: &super::job::Control,
    ) -> Result<Self, String> {
        let input = File::open(path).map_err(|e| e.to_string())?;
        Self::collect_open(input, credentials, home, max_bytes, control)
    }

    pub fn collect_open(
        mut input: File,
        credentials: &[String],
        home: Option<&Path>,
        max_bytes: Option<u64>,
        control: &super::job::Control,
    ) -> Result<Self, String> {
        control.check()?;
        let original_bytes = input.metadata().map_err(|e| e.to_string())?.len();
        let start = original_bytes.saturating_sub(max_bytes.unwrap_or(original_bytes));
        let skip_partial = if start > 0 {
            input
                .seek(SeekFrom::Start(start - 1))
                .map_err(|e| e.to_string())?;
            let mut previous = [0];
            input.read_exact(&mut previous).map_err(|e| e.to_string())?;
            previous[0] != b'\n'
        } else {
            false
        };
        input
            .seek(SeekFrom::Start(start))
            .map_err(|e| e.to_string())?;
        let mut staged = Self {
            staged: super::scratch::Scratch::new()?.track_cleanup(control),
            original_bytes,
            included_bytes: 0,
            tail_start_offset: start,
            read_bytes: u64::from(start > 0),
        };
        super::scratch::ensure_space(staged.staged.path(), original_bytes - start)?;
        // Also protect multiline credentials when processing one log line at a time.
        let credentials = credentials
            .iter()
            .flat_map(|value| value.lines())
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .collect::<Vec<_>>();
        let mut reader = BufReader::with_capacity(64 * 1024, input.take(original_bytes - start));
        if skip_partial {
            loop {
                control.check()?;
                let chunk = reader.fill_buf().map_err(|e| e.to_string())?;
                if chunk.is_empty() {
                    break;
                }
                let found = chunk.iter().position(|byte| *byte == b'\n');
                let count = found.map_or(chunk.len(), |index| index + 1);
                reader.consume(count);
                staged.tail_start_offset += count as u64;
                staged.read_bytes += count as u64;
                control.advance(count as u64)?;
                if found.is_some() {
                    break;
                }
            }
        }
        let mut writer = BufWriter::with_capacity(256 * 1024, &mut staged.staged.file);
        let mut raw = Vec::new();
        let mut read_bytes = 0_u64;
        let mut first = true;
        loop {
            raw.clear();
            // Check cancellation even in a very long record without a newline.
            loop {
                control.check()?;
                let chunk = reader.fill_buf().map_err(|e| e.to_string())?;
                if chunk.is_empty() {
                    break;
                }
                let end = chunk.iter().position(|byte| *byte == b'\n');
                let count = end.map_or(chunk.len(), |index| index + 1);
                raw.extend_from_slice(&chunk[..count]);
                reader.consume(count);
                control.advance(count as u64)?;
                if end.is_some() {
                    break;
                }
            }
            let count = raw.len();
            if count == 0 {
                break;
            }
            read_bytes += count as u64;
            let raw_text = String::from_utf8_lossy(&raw);
            let (text, _) = super::redact_text(&raw_text, &credentials, home);
            let text = redact_boundary_fragments(&text, &credentials, first, !raw.ends_with(b"\n"));
            if !first {
                writer.write_all(b"\n").map_err(|e| e.to_string())?;
                staged.included_bytes += 1;
            }
            first = false;
            writer
                .write_all(text.as_bytes())
                .map_err(|e| e.to_string())?;
            staged.included_bytes += text.len();
        }
        writer.flush().map_err(|e| e.to_string())?;
        drop(writer);
        staged.read_bytes += read_bytes;
        if read_bytes != original_bytes - staged.tail_start_offset {
            return Err("log snapshot was truncated during export".into());
        }
        staged
            .staged
            .file
            .seek(SeekFrom::Start(0))
            .map_err(|e| e.to_string())?;
        Ok(staged)
    }
}

// A byte-based rotation or a concurrently appended prefix can split a credential.
// Mask matching fragments at physical file boundaries as well as whole values.
// Conservative short matches affect only the boundary records, not every log line.
fn redact_boundary_fragments(
    text: &str,
    credentials: &[String],
    first: bool,
    last: bool,
) -> String {
    let mut text = text.to_owned();
    for credential in credentials {
        if first && !text.starts_with(credential) {
            let start = text.trim_start_matches('\u{fffd}');
            if let Some(suffix) = credential
                .char_indices()
                .skip(1)
                .map(|(index, _)| &credential[index..])
                .find(|suffix| start.starts_with(suffix))
            {
                text = format!("<REDACTED:BOUNDARY>{}", &start[suffix.len()..]);
            }
        }
        if last && !text.ends_with(credential) {
            let end = text.trim_end_matches('\u{fffd}');
            if let Some(prefix) = credential
                .char_indices()
                .rev()
                .filter(|(index, _)| *index > 0)
                .map(|(index, _)| &credential[..index])
                .find(|prefix| end.ends_with(prefix))
            {
                text = format!("{}<REDACTED:BOUNDARY>", &end[..end.len() - prefix.len()]);
            }
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn opened_snapshot_survives_rotation_without_reopening_the_new_file() {
        let root =
            std::env::temp_dir().join(format!("af-log-snapshot-{:032x}", rand::random::<u128>()));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("current.log");
        let old = root.join("old.log");
        fs::write(&path, b"original\n").unwrap();
        let input = File::open(&path).unwrap();
        fs::rename(&path, &old).unwrap();
        fs::write(&path, b"replacement\n").unwrap();
        let mut staged = StagedLog::collect_open(
            input,
            &[],
            None,
            None,
            &super::super::job::Control::detached(),
        )
        .unwrap();
        let mut text = String::new();
        staged.staged.file.read_to_string(&mut text).unwrap();
        assert_eq!(text, "original");
        drop(staged);
        fs::remove_file(path).unwrap();
        fs::remove_file(old).unwrap();
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn credentials_split_by_rotation_are_redacted_on_both_sides() {
        let credentials = vec!["secret-value-1234".to_string()];
        let left = redact_boundary_fragments("ordinary secret-value-", &credentials, false, true);
        let right =
            redact_boundary_fragments("1234 followed by public text\n", &credentials, true, false);
        assert_eq!(left, "ordinary <REDACTED:BOUNDARY>");
        assert_eq!(right, "<REDACTED:BOUNDARY> followed by public text\n");
        assert_eq!(
            redact_boundary_fragments("ordinary line\n", &credentials, false, false),
            "ordinary line\n"
        );
    }

    #[test]
    fn tail_reads_only_budget_and_drops_partial_record() {
        let path =
            std::env::temp_dir().join(format!("af-tail-test-{:032x}", rand::random::<u128>()));
        fs::write(&path, b"OLD-DATA\nSECRET-PARTIAL\nkeep\n").unwrap();
        let mut staged = StagedLog::collect_range(
            &path,
            &[],
            None,
            Some(12),
            &super::super::job::Control::detached(),
        )
        .unwrap();
        let mut text = String::new();
        staged.staged.file.read_to_string(&mut text).unwrap();
        assert_eq!(text, "keep");
        assert!(staged.read_bytes <= 13);
        assert_eq!(staged.tail_start_offset, 24);
        drop(staged);
        fs::remove_file(path).unwrap();
    }

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
        let temporary = staged.staged.path().to_owned();
        let mut text = String::new();
        staged.staged.file.read_to_string(&mut text).unwrap();
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
