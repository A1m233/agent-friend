use super::model::{ShellDiagnosticArtifact, ShellJournalRecord};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    time::Duration,
};

const ACTIVE_FILE_NAME: &str = "events.jsonl";
const MAX_SEGMENT_BYTES: u64 = 2 * 1024 * 1024;
const MAX_FILES: usize = 4;
const MAX_AGE: Duration = Duration::from_secs(30 * 24 * 60 * 60);

#[derive(Clone, Copy)]
struct JournalLimits {
    max_segment_bytes: u64,
    max_files: usize,
    max_age: Duration,
}

impl Default for JournalLimits {
    fn default() -> Self {
        Self {
            max_segment_bytes: MAX_SEGMENT_BYTES,
            max_files: MAX_FILES,
            max_age: MAX_AGE,
        }
    }
}

pub(crate) struct ShellJournal {
    directory: PathBuf,
    limits: JournalLimits,
    next_sequence: u64,
}

impl ShellJournal {
    pub(crate) fn open(directory: PathBuf) -> Result<Self, String> {
        Self::open_with_limits(directory, JournalLimits::default())
    }

    fn open_with_limits(directory: PathBuf, limits: JournalLimits) -> Result<Self, String> {
        fs::create_dir_all(&directory)
            .map_err(|error| format!("create shell diagnostics log directory failed: {error}"))?;
        let journal = Self {
            directory,
            limits,
            next_sequence: 1,
        };
        journal.prune_expired()?;
        Ok(journal)
    }

    pub(crate) fn next_sequence(&mut self) -> u64 {
        let sequence = self.next_sequence;
        self.next_sequence = self.next_sequence.saturating_add(1);
        sequence
    }

    pub(crate) fn append(&self, record: &ShellJournalRecord) -> Result<(), String> {
        let mut bytes = serde_json::to_vec(record)
            .map_err(|error| format!("serialize shell diagnostics record failed: {error}"))?;
        bytes.push(b'\n');
        self.append_bytes(&bytes)
    }

    pub(crate) fn export_artifacts(&self) -> (Vec<ShellDiagnosticArtifact>, Vec<String>) {
        let mut artifacts = Vec::new();
        let mut failures = Vec::new();
        let mut paths = self.paths_oldest_first();
        paths.retain(|path| path.is_file());

        for (index, path) in paths.into_iter().enumerate() {
            match fs::read(&path) {
                Ok(bytes) => artifacts.push(ShellDiagnosticArtifact {
                    name: format!("diagnostics/windows-shell/events-{:03}.jsonl", index + 1),
                    bytes,
                }),
                Err(error) => failures.push(format!(
                    "read shell diagnostics journal segment {} failed: {error}",
                    path.file_name()
                        .and_then(|name| name.to_str())
                        .unwrap_or("<unknown>")
                )),
            }
        }

        (artifacts, failures)
    }

    fn append_bytes(&self, bytes: &[u8]) -> Result<(), String> {
        let active = self.active_path();
        let current_bytes = active
            .metadata()
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        if current_bytes > 0
            && current_bytes.saturating_add(bytes.len() as u64) > self.limits.max_segment_bytes
        {
            self.rotate()?;
        }

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&active)
            .map_err(|error| format!("open shell diagnostics journal failed: {error}"))?;
        file.write_all(bytes)
            .and_then(|_| file.flush())
            .map_err(|error| format!("write shell diagnostics journal failed: {error}"))
    }

    fn rotate(&self) -> Result<(), String> {
        if self.limits.max_files <= 1 {
            let active = self.active_path();
            if active.exists() {
                fs::remove_file(active).map_err(|error| {
                    format!("replace shell diagnostics journal failed: {error}")
                })?;
            }
            return Ok(());
        }

        for index in (1..self.limits.max_files).rev() {
            let destination = self.segment_path(index);
            if index == self.limits.max_files - 1 && destination.exists() {
                fs::remove_file(&destination).map_err(|error| {
                    format!("remove oldest shell diagnostics journal failed: {error}")
                })?;
            }
            let source = if index == 1 {
                self.active_path()
            } else {
                self.segment_path(index - 1)
            };
            if source.exists() {
                fs::rename(&source, &destination)
                    .map_err(|error| format!("rotate shell diagnostics journal failed: {error}"))?;
            }
        }
        Ok(())
    }

    fn prune_expired(&self) -> Result<(), String> {
        for path in self.paths_oldest_first() {
            let Ok(modified) = path.metadata().and_then(|metadata| metadata.modified()) else {
                continue;
            };
            let Ok(age) = modified.elapsed() else {
                continue;
            };
            if age > self.limits.max_age {
                fs::remove_file(&path).map_err(|error| {
                    format!("prune expired shell diagnostics journal failed: {error}")
                })?;
            }
        }
        Ok(())
    }

    fn paths_oldest_first(&self) -> Vec<PathBuf> {
        let mut paths = (1..self.limits.max_files)
            .rev()
            .map(|index| self.segment_path(index))
            .collect::<Vec<_>>();
        paths.push(self.active_path());
        paths
    }

    fn active_path(&self) -> PathBuf {
        self.directory.join(ACTIVE_FILE_NAME)
    }

    fn segment_path(&self, index: usize) -> PathBuf {
        self.directory.join(format!("{ACTIVE_FILE_NAME}.{index}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn test_directory(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "agent-friend-shell-diagnostics-{name}-{}-{nonce}",
            std::process::id()
        ))
    }

    #[test]
    fn rotation_is_bounded_and_exported_oldest_first() {
        let directory = test_directory("rotation");
        let journal = ShellJournal::open_with_limits(
            directory.clone(),
            JournalLimits {
                max_segment_bytes: 18,
                max_files: 3,
                max_age: Duration::from_secs(60),
            },
        )
        .expect("open journal");

        for value in [
            "record-one\n",
            "record-two\n",
            "record-three\n",
            "record-four\n",
        ] {
            journal.append_bytes(value.as_bytes()).expect("append");
        }

        let (artifacts, failures) = journal.export_artifacts();
        assert!(failures.is_empty());
        assert_eq!(artifacts.len(), 3);
        let combined = artifacts
            .iter()
            .map(|artifact| String::from_utf8_lossy(&artifact.bytes))
            .collect::<String>();
        assert!(!combined.contains("record-one"));
        assert!(combined.find("record-two") < combined.find("record-four"));

        fs::remove_dir_all(directory).expect("cleanup test journal");
    }
}
