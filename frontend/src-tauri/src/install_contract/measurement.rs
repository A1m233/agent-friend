use serde::Serialize;

/// Aggregates only; no file paths, credentials, or per-file journal writes.
/// Timings are disjoint subsets of total_ms, not additional startup phases.
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct VerificationMetrics {
    pub total_ms: f64,
    pub metadata_ms: f64,
    pub read_ms: f64,
    pub hash_ms: f64,
    pub manifest_files: usize,
    pub metadata_checked: usize,
    pub files_verified: usize,
    pub bytes_read: u64,
}
