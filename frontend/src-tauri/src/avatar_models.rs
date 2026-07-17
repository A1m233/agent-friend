use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
};
use tauri::{path::BaseDirectory, Manager};

use crate::paths;

const BUILTIN_MANIFEST_SCHEMA_VERSION: u32 = 1;
const BUILTIN_BUNDLE_VERSION: &str = "040-2026-06-30";
const RESOURCE_SEED_ROOT: &str = "avatar-seed";
const LIVE2D_HIYORI_ID: &str = "builtin:live2d:hiyori";

static BUILTIN_INSTALL_LOCK: Mutex<()> = Mutex::new(());

const ARK_SPINE_SLUGS: &[&str] = &[
    "002_amiya",
    "4202_haruka",
    "140_whitew",
    "103_angel",
    "358_lisa",
    "102_texas",
    "263_skadi",
    "003_kalts",
    "1035_wisdel",
    "4133_logos",
    "2027_wang",
    "2025_shu",
    "4179_monstr",
    "010_chen",
];

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct BuiltinManifest {
    schema_version: u32,
    bundle_version: String,
    files: BTreeMap<String, u64>,
}

#[derive(Clone, Debug)]
struct SeedDir {
    source: PathBuf,
    target_rel: PathBuf,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstalledAvatarModelResources {
    id: String,
    backend: String,
    resources: serde_json::Value,
    available: bool,
    error: Option<String>,
}

fn builtin_dir() -> PathBuf {
    paths::builtin_avatar_models_dir()
}

fn manifest_path() -> PathBuf {
    builtin_dir().join("manifest.json")
}

fn normalize_rel(path: &Path) -> String {
    path.components()
        .map(|part| part.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/")
}

fn collect_files(
    source_root: &Path,
    target_rel_root: &Path,
    manifest_files: &mut BTreeMap<String, u64>,
) -> Result<Vec<(PathBuf, PathBuf, u64)>, String> {
    fn visit(
        dir: &Path,
        source_root: &Path,
        target_rel_root: &Path,
        manifest_files: &mut BTreeMap<String, u64>,
        out: &mut Vec<(PathBuf, PathBuf, u64)>,
    ) -> Result<(), String> {
        for entry in fs::read_dir(dir).map_err(|e| format!("read seed dir {dir:?} failed: {e}"))? {
            let entry = entry.map_err(|e| format!("read seed entry in {dir:?} failed: {e}"))?;
            let path = entry.path();
            let metadata = entry
                .metadata()
                .map_err(|e| format!("read seed metadata {path:?} failed: {e}"))?;
            if metadata.is_dir() {
                visit(&path, source_root, target_rel_root, manifest_files, out)?;
                continue;
            }
            if !metadata.is_file() {
                continue;
            }
            let rel = path.strip_prefix(source_root).map_err(|e| {
                format!("strip seed prefix {source_root:?} from {path:?} failed: {e}")
            })?;
            let target_rel = target_rel_root.join(rel);
            let size = metadata.len();
            manifest_files.insert(normalize_rel(&target_rel), size);
            out.push((path, target_rel, size));
        }
        Ok(())
    }

    let mut out = Vec::new();
    visit(
        source_root,
        source_root,
        target_rel_root,
        manifest_files,
        &mut out,
    )?;
    Ok(out)
}

fn read_manifest(path: &Path) -> Option<BuiltinManifest> {
    let raw = fs::read_to_string(path).ok()?;
    serde_json::from_str(&raw).ok()
}

fn write_manifest(path: &Path, manifest: &BuiltinManifest) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("create manifest parent {parent:?} failed: {e}"))?;
    }
    let raw = serde_json::to_string_pretty(manifest)
        .map_err(|e| format!("serialize manifest failed: {e}"))?;
    fs::write(path, raw).map_err(|e| format!("write manifest {path:?} failed: {e}"))
}

fn copy_seed_files(seed_dirs: &[SeedDir]) -> Result<BuiltinManifest, String> {
    let target_root = builtin_dir();
    let mut files = BTreeMap::new();
    let mut pending = Vec::new();

    for seed in seed_dirs {
        if !seed.source.is_dir() {
            return Err(format!("avatar seed dir missing: {:?}", seed.source));
        }
        pending.extend(collect_files(&seed.source, &seed.target_rel, &mut files)?);
    }

    for (source, target_rel, expected_len) in pending {
        let target = target_root.join(&target_rel);
        let should_copy = match fs::metadata(&target) {
            Ok(metadata) => !metadata.is_file() || metadata.len() != expected_len,
            Err(_) => true,
        };
        if !should_copy {
            continue;
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("create model asset parent {parent:?} failed: {e}"))?;
        }
        fs::copy(&source, &target)
            .map_err(|e| format!("copy avatar seed {source:?} -> {target:?} failed: {e}"))?;
    }

    let manifest = BuiltinManifest {
        schema_version: BUILTIN_MANIFEST_SCHEMA_VERSION,
        bundle_version: BUILTIN_BUNDLE_VERSION.to_string(),
        files,
    };
    write_manifest(&manifest_path(), &manifest)?;
    Ok(manifest)
}

fn manifest_matches_installed(manifest: &BuiltinManifest) -> bool {
    if manifest.schema_version != BUILTIN_MANIFEST_SCHEMA_VERSION
        || manifest.bundle_version != BUILTIN_BUNDLE_VERSION
    {
        return false;
    }
    let target_root = builtin_dir();
    manifest.files.iter().all(|(rel, expected_len)| {
        fs::metadata(target_root.join(rel))
            .map(|metadata| metadata.is_file() && metadata.len() == *expected_len)
            .unwrap_or(false)
    })
}

fn dev_public_dir(relative: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("public")
        .join(relative)
}

fn resolve_seed_dir(app: &tauri::AppHandle, resource_rel: &str, dev_public_rel: &str) -> PathBuf {
    let resource = app
        .path()
        .resolve(
            format!("{RESOURCE_SEED_ROOT}/{resource_rel}"),
            BaseDirectory::Resource,
        )
        .ok();
    if let Some(path) = resource {
        if path.is_dir() {
            return path;
        }
    }
    dev_public_dir(dev_public_rel)
}

fn seed_dirs(app: &tauri::AppHandle) -> Vec<SeedDir> {
    vec![
        SeedDir {
            source: resolve_seed_dir(app, "live2d/hiyori", "live2d-models/hiyori"),
            target_rel: PathBuf::from("live2d").join("hiyori"),
        },
        SeedDir {
            source: resolve_seed_dir(app, "spine/ark-models", "spine-models/ark-models"),
            target_rel: PathBuf::from("spine").join("ark-models"),
        },
    ]
}

pub fn ensure_builtin_avatar_models_installed(app: &tauri::AppHandle) -> Result<(), String> {
    let _guard = BUILTIN_INSTALL_LOCK
        .lock()
        .map_err(|_| "avatar model install lock poisoned".to_string())?;
    let path = manifest_path();
    if let Some(manifest) = read_manifest(&path) {
        if manifest_matches_installed(&manifest) {
            log::info!(
                "avatar models: builtin assets already installed at {:?}",
                builtin_dir()
            );
            return Ok(());
        }
    }

    let manifest = copy_seed_files(&seed_dirs(app))?;
    log::info!(
        "avatar models: installed builtin assets at {:?}; files={}",
        builtin_dir(),
        manifest.files.len()
    );
    Ok(())
}

fn path_string(path: PathBuf) -> String {
    path.to_string_lossy().to_string()
}

fn live2d_resources() -> InstalledAvatarModelResources {
    let model3 = builtin_dir()
        .join("live2d")
        .join("hiyori")
        .join("Hiyori.model3.json");
    let available = model3.is_file();
    InstalledAvatarModelResources {
        id: LIVE2D_HIYORI_ID.to_string(),
        backend: "live2d".to_string(),
        resources: serde_json::json!({
            "model3Path": path_string(model3),
        }),
        available,
        error: (!available).then(|| "missing Hiyori.model3.json".to_string()),
    }
}

fn spine_resources(slug: &str) -> InstalledAvatarModelResources {
    let dir = builtin_dir().join("spine").join("ark-models").join(slug);
    let base = format!("build_char_{slug}");
    let skeleton = dir.join(format!("{base}.skel"));
    let atlas = dir.join(format!("{base}.atlas"));
    let texture = dir.join(format!("{base}.png"));
    let missing = [
        (&skeleton, "skeleton"),
        (&atlas, "atlas"),
        (&texture, "texture"),
    ]
    .iter()
    .filter_map(|(path, label)| (!path.is_file()).then_some(*label))
    .collect::<Vec<_>>();
    let available = missing.is_empty();
    InstalledAvatarModelResources {
        id: format!("builtin:spine:ark-models:{slug}"),
        backend: "spine".to_string(),
        resources: serde_json::json!({
            "skeletonPath": path_string(skeleton),
            "atlasPath": path_string(atlas),
            "texturePaths": [path_string(texture)],
        }),
        available,
        error: (!available).then(|| format!("missing {}", missing.join(", "))),
    }
}

#[tauri::command]
pub fn get_installed_avatar_model_resources(
    app: tauri::AppHandle,
) -> Result<Vec<InstalledAvatarModelResources>, String> {
    // Configured webviews can request the catalog while Tauri setup is still installing the
    // bundled seed files. Waiting on the same install lock prevents a first-launch empty catalog
    // from being cached by the pet window until the next process restart.
    ensure_builtin_avatar_models_installed(&app)?;
    let mut resources = Vec::with_capacity(1 + ARK_SPINE_SLUGS.len());
    resources.push(live2d_resources());
    resources.extend(ARK_SPINE_SLUGS.iter().map(|slug| spine_resources(slug)));
    Ok(resources)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "agent-friend-avatar-models-{name}-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn collect_files_records_target_relative_lengths() {
        let dir = temp_dir("collect");
        let source = dir.join("source");
        fs::create_dir_all(source.join("nested")).expect("create nested");
        fs::write(source.join("nested").join("model.txt"), "abc").expect("write seed");

        let mut manifest_files = BTreeMap::new();
        let files = collect_files(&source, Path::new("live2d/hiyori"), &mut manifest_files)
            .expect("collect");

        assert_eq!(files.len(), 1);
        assert_eq!(
            manifest_files.get("live2d/hiyori/nested/model.txt"),
            Some(&3)
        );

        fs::remove_dir_all(dir).expect("cleanup");
    }
}
