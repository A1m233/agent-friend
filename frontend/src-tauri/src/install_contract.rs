use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Component, Path, PathBuf},
    time::Instant,
};

mod measurement;
pub use measurement::VerificationMetrics;

pub const ACTIVE_INSTALL_FILE: &str = "active-install.json";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BuildIdentity {
    pub schema_version: u32,
    pub product_version: String,
    pub build_id: String,
    pub source_commit: String,
    pub source_tree_sha256: String,
    pub dirty: bool,
    pub build_mode: String,
    pub distribution: String,
    pub profile_id: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallProfile {
    pub schema_version: u32,
    pub profile_id: String,
    pub instance_domain: String,
    pub product_name: String,
    pub bundle_identifier: String,
    pub main_binary_name: String,
    pub install_dir_name: String,
    pub registry_product_name: String,
    pub autostart_value_name: String,
    pub data_namespace: String,
    pub settings_namespace: String,
    pub log_namespace: String,
    pub keyring_service: String,
    pub bridge_port: u16,
    pub voice_port: u16,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActiveInstallReceipt {
    pub schema_version: u32,
    pub profile_id: String,
    pub build_identity_sha256: String,
    pub build_id: String,
    pub payload_relative_path: String,
    pub payload_manifest_sha256: String,
    pub activated_at: String,
    pub transaction_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PayloadManifest {
    schema_version: u32,
    build_identity_sha256: String,
    profile_id: String,
    files: Vec<PayloadFile>,
}

#[derive(Clone, Debug, Deserialize)]
struct PayloadFile {
    path: String,
    size: u64,
    sha256: String,
    capability: String,
}

#[derive(Clone, Debug)]
pub struct ActivePayload {
    pub install_root: PathBuf,
    pub payload_root: PathBuf,
    pub runtime_root: PathBuf,
    pub receipt: ActiveInstallReceipt,
    pub identity: BuildIdentity,
    pub identity_sha256: String,
    pub runtime_executable_sha256: String,
    pub profile: InstallProfile,
}

#[derive(Clone, Debug)]
pub struct VerifiedPayload {
    pub payload_root: PathBuf,
    pub identity: BuildIdentity,
    pub identity_sha256: String,
    pub manifest_sha256: String,
    pub runtime_executable_sha256: String,
}

pub(crate) fn production_profile() -> InstallProfile {
    InstallProfile {
        schema_version: 1,
        profile_id: "production".to_string(),
        instance_domain: "agent-friend-production".to_string(),
        product_name: "agent-friend".to_string(),
        bundle_identifier: "com.agentfriend.desktop".to_string(),
        main_binary_name: "agent-friend".to_string(),
        install_dir_name: "agent-friend".to_string(),
        registry_product_name: "agent-friend".to_string(),
        autostart_value_name: "agent-friend".to_string(),
        data_namespace: "agent-friend".to_string(),
        settings_namespace: "com.agentfriend.desktop".to_string(),
        log_namespace: "agent-friend".to_string(),
        keyring_service: "agent-friend".to_string(),
        bridge_port: 18800,
        voice_port: 18900,
    }
}

pub fn embedded_profile() -> Result<InstallProfile, String> {
    match option_env!("AGENT_FRIEND_INSTALL_PROFILE_JSON") {
        Some(raw) => serde_json::from_str(raw)
            .map_err(|error| format!("install_integrity_profile_invalid: {error}")),
        None => Ok(production_profile()),
    }
}

pub fn embedded_identity() -> Result<Option<(BuildIdentity, String)>, String> {
    let Some(raw) = option_env!("AGENT_FRIEND_BUILD_IDENTITY_JSON") else {
        return Ok(None);
    };
    let identity: BuildIdentity = serde_json::from_str(raw)
        .map_err(|error| format!("install_integrity_shell_identity_invalid: {error}"))?;
    let hash = sha256_bytes(format!("{raw}\n").as_bytes());
    Ok(Some((identity, hash)))
}

fn sha256_bytes(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn sha256_file(path: &Path, metrics: &mut VerificationMetrics) -> Result<String, String> {
    let start = Instant::now();
    let read = fs::read(path);
    metrics.read_ms += start.elapsed().as_secs_f64() * 1000.0;
    let bytes = read.map_err(|error| {
        format!(
            "install_integrity_file_unreadable: {}: {error}",
            path.display()
        )
    })?;
    metrics.bytes_read += bytes.len() as u64;
    let start = Instant::now();
    let digest = sha256_bytes(&bytes);
    metrics.hash_ms += start.elapsed().as_secs_f64() * 1000.0;
    Ok(digest)
}

fn safe_relative_path(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(format!("install_integrity_unsafe_path: {value}"));
    }
    Ok(path.to_path_buf())
}

pub fn verify_candidate_payload(
    install_root: &Path,
    payload_relative_path: &str,
    expected_profile_id: &str,
    expected_build_id: &str,
) -> Result<VerifiedPayload, String> {
    verify_candidate_payload_with_metrics(
        install_root,
        payload_relative_path,
        expected_profile_id,
        expected_build_id,
        &mut VerificationMetrics::default(),
    )
}

/// Same validation and short-circuit behavior as the normal entry point.
/// Metrics survive failures so a partial scan cannot masquerade as a full one.
pub fn verify_candidate_payload_with_metrics(
    install_root: &Path,
    payload_relative_path: &str,
    expected_profile_id: &str,
    expected_build_id: &str,
    metrics: &mut VerificationMetrics,
) -> Result<VerifiedPayload, String> {
    *metrics = VerificationMetrics::default();
    let start = Instant::now();
    let result = verify_candidate_payload_inner(
        install_root,
        payload_relative_path,
        expected_profile_id,
        expected_build_id,
        metrics,
    );
    metrics.total_ms = start.elapsed().as_secs_f64() * 1000.0;
    result
}

fn verify_candidate_payload_inner(
    install_root: &Path,
    payload_relative_path: &str,
    expected_profile_id: &str,
    expected_build_id: &str,
    metrics: &mut VerificationMetrics,
) -> Result<VerifiedPayload, String> {
    let install_root = install_root
        .canonicalize()
        .map_err(|error| format!("install_integrity_root_unreadable: {error}"))?;
    let payload_root = install_root
        .join(safe_relative_path(payload_relative_path)?)
        .canonicalize()
        .map_err(|error| format!("install_integrity_payload_unreadable: {error}"))?;
    if !payload_root.starts_with(&install_root) {
        return Err("install_integrity_payload_escaped_root".to_string());
    }

    let identity_bytes = fs::read(payload_root.join("build-identity.json"))
        .map_err(|error| format!("install_integrity_payload_identity_unreadable: {error}"))?;
    let identity_sha256 = sha256_bytes(&identity_bytes);
    let identity: BuildIdentity = serde_json::from_slice(&identity_bytes)
        .map_err(|error| format!("install_integrity_payload_identity_invalid: {error}"))?;
    if identity.schema_version != 1
        || identity.profile_id != expected_profile_id
        || identity.build_id != expected_build_id
    {
        return Err("install_integrity_payload_identity_mismatch".to_string());
    }

    let manifest_path = payload_root.join("payload-manifest.json");
    let manifest_bytes = fs::read(&manifest_path)
        .map_err(|error| format!("install_integrity_manifest_unreadable: {error}"))?;
    let manifest_sha256 = sha256_bytes(&manifest_bytes);
    let manifest: PayloadManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|error| format!("install_integrity_manifest_invalid: {error}"))?;
    if manifest.schema_version != 1
        || manifest.profile_id != expected_profile_id
        || manifest.build_identity_sha256 != identity_sha256
    {
        return Err("install_integrity_manifest_identity_mismatch".to_string());
    }

    let mut runtime_executable_sha256 = None;
    let mut has_models = false;
    let mut seen = std::collections::HashSet::new();
    metrics.manifest_files = manifest.files.len();
    for item in &manifest.files {
        let relative = safe_relative_path(&item.path)?;
        let folded = item.path.replace('\\', "/").to_lowercase();
        if !seen.insert(folded) {
            return Err(format!("install_integrity_duplicate_path: {}", item.path));
        }
        let path = payload_root.join(relative);
        let start = Instant::now();
        let metadata = path.metadata();
        metrics.metadata_ms += start.elapsed().as_secs_f64() * 1000.0;
        let metadata = metadata.map_err(|error| {
            format!(
                "install_integrity_payload_file_missing: {}: {error}",
                item.path
            )
        })?;
        metrics.metadata_checked += 1;
        if !metadata.is_file()
            || metadata.len() != item.size
            || sha256_file(&path, metrics)? != item.sha256
        {
            return Err(format!(
                "install_integrity_payload_hash_mismatch: {}",
                item.path
            ));
        }
        metrics.files_verified += 1;
        if item.path == "runtime/python/agent-friend-runtime.exe"
            && item.capability == "runtime-python"
        {
            runtime_executable_sha256 = Some(item.sha256.clone());
        }
        has_models |=
            item.path.starts_with("runtime/models/") && item.capability == "runtime-models";
    }
    let runtime_executable_sha256 = runtime_executable_sha256
        .ok_or_else(|| "install_integrity_required_capability_missing".to_string())?;
    if !has_models {
        return Err("install_integrity_required_capability_missing".to_string());
    }

    Ok(VerifiedPayload {
        payload_root,
        identity,
        identity_sha256,
        manifest_sha256,
        runtime_executable_sha256,
    })
}

pub fn discover_active_payload(
    resource_dir: Option<&Path>,
) -> Result<Option<ActivePayload>, String> {
    discover_active_payload_with_metrics(resource_dir, &mut VerificationMetrics::default())
}

pub fn discover_active_payload_with_metrics(
    resource_dir: Option<&Path>,
    metrics: &mut VerificationMetrics,
) -> Result<Option<ActivePayload>, String> {
    let Some((identity, identity_sha256)) = embedded_identity()? else {
        return Ok(None);
    };
    let profile = embedded_profile()?;
    let install_root = resource_dir.ok_or_else(|| {
        "install_integrity_resource_dir_unavailable: packaged resource root is missing".to_string()
    })?;
    validate_active_payload_measured(install_root, identity, identity_sha256, &profile, metrics)
        .map(Some)
}

#[cfg(test)]
fn validate_active_payload(
    install_root: &Path,
    shell_identity: BuildIdentity,
    shell_identity_sha256: String,
    profile: &InstallProfile,
) -> Result<ActivePayload, String> {
    validate_active_payload_measured(
        install_root,
        shell_identity,
        shell_identity_sha256,
        profile,
        &mut VerificationMetrics::default(),
    )
}

fn validate_active_payload_measured(
    install_root: &Path,
    shell_identity: BuildIdentity,
    shell_identity_sha256: String,
    profile: &InstallProfile,
    metrics: &mut VerificationMetrics,
) -> Result<ActivePayload, String> {
    let receipt_path = install_root.join(ACTIVE_INSTALL_FILE);
    let receipt: ActiveInstallReceipt = serde_json::from_slice(
        &fs::read(&receipt_path)
            .map_err(|error| format!("install_integrity_receipt_unreadable: {error}"))?,
    )
    .map_err(|error| format!("install_integrity_receipt_invalid: {error}"))?;
    if receipt.schema_version != 1
        || receipt.profile_id != profile.profile_id
        || receipt.profile_id != shell_identity.profile_id
        || receipt.build_id != shell_identity.build_id
        || receipt.build_identity_sha256 != shell_identity_sha256
    {
        return Err("install_integrity_shell_receipt_mismatch".to_string());
    }

    let relative = safe_relative_path(&receipt.payload_relative_path)?;
    let install_root = install_root
        .canonicalize()
        .map_err(|error| format!("install_integrity_root_unreadable: {error}"))?;
    let payload_root = install_root
        .join(relative)
        .canonicalize()
        .map_err(|error| format!("install_integrity_payload_unreadable: {error}"))?;
    if !payload_root.starts_with(&install_root) {
        return Err("install_integrity_payload_escaped_root".to_string());
    }

    let verified = verify_candidate_payload_with_metrics(
        &install_root,
        &receipt.payload_relative_path,
        &profile.profile_id,
        &receipt.build_id,
        metrics,
    )?;
    if verified.manifest_sha256 != receipt.payload_manifest_sha256 {
        return Err("install_integrity_manifest_hash_mismatch".to_string());
    }
    if verified.identity_sha256 != shell_identity_sha256 || verified.identity != shell_identity {
        return Err("install_integrity_payload_identity_mismatch".to_string());
    }

    Ok(ActivePayload {
        install_root,
        runtime_root: payload_root.join("runtime"),
        payload_root,
        receipt,
        identity: shell_identity,
        identity_sha256: shell_identity_sha256,
        runtime_executable_sha256: verified.runtime_executable_sha256,
        profile: profile.clone(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn identity() -> BuildIdentity {
        BuildIdentity {
            schema_version: 1,
            product_version: "1.2.3".to_string(),
            build_id: "build-123".to_string(),
            source_commit: "a".repeat(40),
            source_tree_sha256: "b".repeat(64),
            dirty: false,
            build_mode: "formal".to_string(),
            distribution: "ordinary".to_string(),
            profile_id: "production".to_string(),
        }
    }

    fn temp_root(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "agent-friend-install-contract-{name}-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    #[test]
    fn receipt_identity_mismatch_fails_before_runtime_selection() {
        let root = temp_root("receipt-mismatch");
        fs::create_dir_all(&root).unwrap();
        fs::write(
            root.join(ACTIVE_INSTALL_FILE),
            serde_json::to_vec(&json!({
                "schemaVersion": 1,
                "profileId": "production",
                "buildIdentitySha256": "0".repeat(64),
                "buildId": "old-build",
                "payloadRelativePath": "packages/old-build",
                "payloadManifestSha256": "0".repeat(64),
                "activatedAt": "2026-08-08T00:00:00Z",
                "transactionId": "tx"
            }))
            .unwrap(),
        )
        .unwrap();

        let error =
            validate_active_payload(&root, identity(), "1".repeat(64), &production_profile())
                .expect_err("mismatched receipt must fail");

        assert_eq!(error, "install_integrity_shell_receipt_mismatch");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unsafe_receipt_payload_path_is_rejected() {
        assert!(safe_relative_path("../runtime").is_err());
        assert!(safe_relative_path("C:/runtime").is_err());
        assert_eq!(
            safe_relative_path("packages/build-123").unwrap(),
            PathBuf::from("packages/build-123")
        );
    }

    fn measured_fixture(name: &str) -> PathBuf {
        let root = temp_root(name);
        let payload = root.join("packages/build-123");
        let mut files = Vec::new();
        for (path, capability, bytes) in [
            (
                "runtime/python/agent-friend-runtime.exe",
                "runtime-python",
                b"runtime".as_slice(),
            ),
            (
                "runtime/models/model.bin",
                "runtime-models",
                b"model".as_slice(),
            ),
        ] {
            let target = payload.join(path);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(target, bytes).unwrap();
            files.push(json!({"path": path, "capability": capability, "size": bytes.len(), "sha256": sha256_bytes(bytes)}));
        }
        let identity_bytes = serde_json::to_vec(&identity()).unwrap();
        fs::write(payload.join("build-identity.json"), &identity_bytes).unwrap();
        fs::write(
            payload.join("payload-manifest.json"),
            serde_json::to_vec(&json!({
                "schemaVersion": 1, "profileId": "production",
                "buildIdentitySha256": sha256_bytes(&identity_bytes), "files": files,
            }))
            .unwrap(),
        )
        .unwrap();
        root
    }

    #[test]
    fn measured_verification_checks_all_bytes_and_retains_partial_failure() {
        let root = measured_fixture("metrics");
        let mut metrics = VerificationMetrics::default();
        let verified = verify_candidate_payload_with_metrics(
            &root,
            "packages/build-123",
            "production",
            "build-123",
            &mut metrics,
        )
        .unwrap();
        assert_eq!(metrics.manifest_files, 2);
        assert_eq!(metrics.files_verified, 2);
        assert_eq!(metrics.bytes_read, 12);
        assert!(metrics.total_ms >= metrics.metadata_ms + metrics.read_ms + metrics.hash_ms);
        let normal =
            verify_candidate_payload(&root, "packages/build-123", "production", "build-123")
                .unwrap();
        assert_eq!(normal.manifest_sha256, verified.manifest_sha256);
        // Same-length corruption must still be detected: no metadata-only shortcut.
        fs::write(
            root.join("packages/build-123/runtime/models/model.bin"),
            b"other",
        )
        .unwrap();
        let measured_error = verify_candidate_payload_with_metrics(
            &root,
            "packages/build-123",
            "production",
            "build-123",
            &mut metrics,
        )
        .unwrap_err();
        let normal_error =
            verify_candidate_payload(&root, "packages/build-123", "production", "build-123")
                .unwrap_err();
        assert_eq!(measured_error, normal_error);
        assert!(measured_error.starts_with("install_integrity_payload_hash_mismatch"));
        assert_eq!(metrics.files_verified, 1);
        assert_eq!(metrics.bytes_read, 12);
        fs::remove_file(root.join("packages/build-123/runtime/models/model.bin")).unwrap();
        assert!(verify_candidate_payload_with_metrics(
            &root,
            "packages/build-123",
            "production",
            "build-123",
            &mut metrics
        )
        .unwrap_err()
        .starts_with("install_integrity_payload_file_missing"));
        assert_eq!(metrics.metadata_checked, 1);
        assert_eq!(metrics.bytes_read, 7);
        assert_eq!(metrics.files_verified, 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn measured_verification_rejects_unsafe_and_duplicate_entries() {
        let root = measured_fixture("manifest-rejection");
        let manifest_path = root.join("packages/build-123/payload-manifest.json");
        let original: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
        let mut duplicate = original.clone();
        duplicate["files"][1] = duplicate["files"][0].clone();
        let mut unsafe_path = original;
        unsafe_path["files"][0]["path"] = json!("../outside");
        for (manifest, prefix) in [
            (duplicate, "install_integrity_duplicate_path"),
            (unsafe_path, "install_integrity_unsafe_path"),
        ] {
            fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
            let mut metrics = VerificationMetrics::default();
            let error = verify_candidate_payload_with_metrics(
                &root,
                "packages/build-123",
                "production",
                "build-123",
                &mut metrics,
            )
            .unwrap_err();
            assert!(error.starts_with(prefix));
            assert!(metrics.files_verified < metrics.manifest_files);
        }
        fs::remove_dir_all(root).unwrap();
    }
}
