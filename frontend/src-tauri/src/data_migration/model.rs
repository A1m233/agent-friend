use serde::{Deserialize, Serialize};

pub const CONTAINER_MAGIC: &[u8; 8] = b"AFMIG001";
pub const CONTAINER_VERSION: u32 = 1;
pub const MANIFEST_VERSION: u32 = 1;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum AssetGroup {
    Sessions,
    Memory,
    Persona,
    Settings,
    UiContinuity,
    RuntimeCredentials,
    ImCredentials,
    UserAvatarModels,
    CliHistory,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ManifestEntry {
    pub path: String,
    pub group: AssetGroup,
    pub size: u64,
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Manifest {
    pub schema_version: u32,
    pub source_app_version: String,
    pub created_at: String,
    pub entries: Vec<ManifestEntry>,
}

impl Manifest {
    pub fn new(entries: Vec<ManifestEntry>) -> Self {
        Self {
            schema_version: MANIFEST_VERSION,
            source_app_version: env!("CARGO_PKG_VERSION").to_string(),
            created_at: chrono::Utc::now().to_rfc3339(),
            entries,
        }
    }
}
