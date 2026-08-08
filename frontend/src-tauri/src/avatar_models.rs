use notify_debouncer_full::{
    new_debouncer,
    notify::{RecommendedWatcher, RecursiveMode, Watcher},
    DebounceEventResult, Debouncer, FileIdMap,
};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, RwLock,
    },
    time::Duration,
};
use tauri::{path::BaseDirectory, AppHandle, Emitter, Manager, State};
use tauri_plugin_opener::OpenerExt;

use crate::{install_contract, paths};

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
    #[serde(skip_serializing_if = "Option::is_none")]
    name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    content_revision: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    default_animation: Option<String>,
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
    if let Ok(resource_root) = app.path().resource_dir() {
        if let Ok(Some(active)) = install_contract::discover_active_payload(Some(&resource_root)) {
            let path = active
                .payload_root
                .join(RESOURCE_SEED_ROOT)
                .join(resource_rel);
            if path.is_dir() {
                return path;
            }
        }
    }
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
        name: None,
        content_revision: None,
        default_animation: None,
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
        name: None,
        content_revision: None,
        default_animation: None,
    }
}

const SIDECAR_FILE: &str = "agent-friend.model.json";
const USER_CATALOG_SCHEMA_VERSION: u32 = 1;
const EVENT_SCAN_UPDATED: &str = "avatar-models://scan-updated";
const EVENT_CATALOG_CHANGED: &str = "avatar-models://catalog-changed";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct UserModelSidecar {
    schema_version: u32,
    id: String,
    name: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UserSpineCandidate {
    id: String,
    name: String,
    model_dir: String,
    skeleton_path: String,
    atlas_path: String,
    texture_paths: Vec<String>,
    content_revision: String,
    runtime_version: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UserModelDiagnostic {
    model_dir: String,
    code: String,
    message: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UserModelScanSnapshot {
    candidates: Vec<UserSpineCandidate>,
    diagnostics: Vec<UserModelDiagnostic>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ValidatedUserModel {
    #[serde(flatten)]
    candidate: UserSpineCandidate,
    default_animation: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct UserCatalogFile {
    schema_version: u32,
    models: Vec<ValidatedUserModel>,
}

#[derive(Default)]
struct UserModelState {
    scan: UserModelScanSnapshot,
    validated: BTreeMap<String, ValidatedUserModel>,
}

type UserModelDebouncer = Debouncer<RecommendedWatcher, FileIdMap>;

pub struct AvatarModelManager {
    state: RwLock<UserModelState>,
    debouncer: Mutex<Option<UserModelDebouncer>>,
    scanning: AtomicBool,
    dirty_again: AtomicBool,
}

impl AvatarModelManager {
    pub fn new() -> Self {
        let validated = read_user_catalog()
            .map(|catalog| {
                catalog
                    .models
                    .into_iter()
                    .map(|model| (model.candidate.id.clone(), model))
                    .collect()
            })
            .unwrap_or_default();
        Self {
            state: RwLock::new(UserModelState {
                scan: UserModelScanSnapshot::default(),
                validated,
            }),
            debouncer: Mutex::new(None),
            scanning: AtomicBool::new(false),
            dirty_again: AtomicBool::new(false),
        }
    }

    pub fn start(self: &Arc<Self>, app: AppHandle) -> Result<(), String> {
        ensure_user_model_root()?;
        self.request_scan(app.clone());
        let manager = Arc::clone(self);
        let callback_app = app.clone();
        let mut debouncer = new_debouncer(
            Duration::from_millis(700),
            None,
            move |result: DebounceEventResult| match result {
                Ok(_) => manager.request_scan(callback_app.clone()),
                Err(errors) => {
                    log::warn!("avatar models: watcher errors: {errors:?}");
                    manager.request_scan(callback_app.clone());
                }
            },
        )
        .map_err(|error| format!("create user model watcher failed: {error}"))?;
        let root = paths::user_spine38_avatar_models_dir();
        let watch_root = root
            .parent()
            .ok_or("user model directory has no parent")?
            .to_path_buf();
        debouncer
            .watcher()
            .watch(&watch_root, RecursiveMode::Recursive)
            .map_err(|error| format!("watch {watch_root:?} failed: {error}"))?;
        debouncer
            .cache()
            .add_root(&watch_root, RecursiveMode::Recursive);
        *self
            .debouncer
            .lock()
            .map_err(|_| "avatar model watcher lock poisoned".to_string())? = Some(debouncer);
        Ok(())
    }

    fn request_scan(self: &Arc<Self>, app: AppHandle) {
        if self.scanning.swap(true, Ordering::AcqRel) {
            self.dirty_again.store(true, Ordering::Release);
            return;
        }
        let manager = Arc::clone(self);
        std::thread::spawn(move || loop {
            manager.dirty_again.store(false, Ordering::Release);
            match scan_user_models(&manager.state) {
                Ok(snapshot) => {
                    let _ = app.emit(EVENT_SCAN_UPDATED, &snapshot);
                    let _ = app.emit(EVENT_CATALOG_CHANGED, serde_json::json!({}));
                }
                Err(error) => log::error!("avatar models: user scan failed: {error}"),
            }
            if !manager.dirty_again.swap(false, Ordering::AcqRel) {
                manager.scanning.store(false, Ordering::Release);
                if manager.dirty_again.swap(false, Ordering::AcqRel)
                    && !manager.scanning.swap(true, Ordering::AcqRel)
                {
                    continue;
                }
                break;
            }
        });
    }
}

fn ensure_user_model_root() -> Result<PathBuf, String> {
    let root = paths::user_spine38_avatar_models_dir();
    fs::create_dir_all(&root)
        .map_err(|error| format!("create user model directory {root:?} failed: {error}"))?;
    Ok(root)
}

fn read_user_catalog() -> Option<UserCatalogFile> {
    let raw = fs::read_to_string(paths::avatar_model_catalog_path()).ok()?;
    serde_json::from_str(&raw).ok()
}

fn write_user_catalog(state: &UserModelState) -> Result<(), String> {
    let path = paths::avatar_model_catalog_path();
    let catalog = UserCatalogFile {
        schema_version: USER_CATALOG_SCHEMA_VERSION,
        models: state.validated.values().cloned().collect(),
    };
    let raw = serde_json::to_string_pretty(&catalog)
        .map_err(|error| format!("serialize user model catalog failed: {error}"))?;
    let temporary = path.with_extension("json.tmp");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("create catalog parent {parent:?} failed: {error}"))?;
    }
    fs::write(&temporary, raw)
        .map_err(|error| format!("write catalog temporary file failed: {error}"))?;
    match fs::rename(&temporary, &path) {
        Ok(()) => Ok(()),
        Err(first_error) if path.exists() => {
            fs::remove_file(&path)
                .map_err(|error| format!("remove old catalog {path:?} failed: {error}"))?;
            fs::rename(&temporary, &path).map_err(|error| {
                format!("replace catalog {path:?} failed after {first_error}: {error}")
            })
        }
        Err(error) => Err(format!("replace catalog {path:?} failed: {error}")),
    }
}

fn random_user_model_id() -> String {
    let mut bytes = [0_u8; 16];
    rand::thread_rng().fill_bytes(&mut bytes);
    format!(
        "user:spine38:{}",
        bytes
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    )
}

fn collect_model_files(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), String> {
    for entry in fs::read_dir(dir).map_err(|error| format!("read {dir:?} failed: {error}"))? {
        let entry = entry.map_err(|error| format!("read entry in {dir:?} failed: {error}"))?;
        let path = entry.path();
        let metadata = entry
            .metadata()
            .map_err(|error| format!("read metadata {path:?} failed: {error}"))?;
        if metadata.is_dir() {
            collect_model_files(&path, out)?;
        } else if metadata.is_file()
            && path.file_name().and_then(|name| name.to_str()) != Some(SIDECAR_FILE)
        {
            out.push(path);
        }
    }
    Ok(())
}

fn read_binary_string(bytes: &[u8], offset: &mut usize) -> Result<String, String> {
    let mut value = 0_u32;
    for shift in (0..35).step_by(7) {
        let byte = *bytes.get(*offset).ok_or("truncated Spine binary header")?;
        *offset += 1;
        value |= u32::from(byte & 0x7f) << shift;
        if byte & 0x80 == 0 {
            if value <= 1 {
                return Ok(String::new());
            }
            let length = (value - 1) as usize;
            let end = offset
                .checked_add(length)
                .ok_or("invalid Spine string length")?;
            let raw = bytes
                .get(*offset..end)
                .ok_or("truncated Spine version string")?;
            *offset = end;
            return std::str::from_utf8(raw)
                .map(str::to_owned)
                .map_err(|error| format!("invalid Spine header utf-8: {error}"));
        }
    }
    Err("invalid Spine binary varint".to_string())
}

fn read_spine_version(path: &Path) -> Result<String, String> {
    let bytes =
        fs::read(path).map_err(|error| format!("read skeleton {path:?} failed: {error}"))?;
    let mut offset = 0;
    let _hash = read_binary_string(&bytes, &mut offset)?;
    read_binary_string(&bytes, &mut offset)
}

fn atlas_texture_pages(path: &Path) -> Result<Vec<String>, String> {
    let raw =
        fs::read_to_string(path).map_err(|error| format!("read atlas {path:?} failed: {error}"))?;
    let lines = raw.lines().map(str::trim).collect::<Vec<_>>();
    let mut pages = Vec::new();
    for pair in lines.windows(2) {
        if !pair[0].is_empty() && !pair[0].contains(':') && pair[1].starts_with("size:") {
            pages.push(pair[0].to_string());
        }
    }
    if pages.is_empty() {
        return Err("atlas does not declare any texture page".to_string());
    }
    Ok(pages)
}

fn content_revision(paths: &[PathBuf]) -> Result<String, String> {
    let mut sorted = paths.to_vec();
    sorted.sort();
    let mut digest = Sha256::new();
    for path in sorted {
        digest.update(path.to_string_lossy().as_bytes());
        digest.update(fs::read(&path).map_err(|error| format!("hash {path:?} failed: {error}"))?);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn load_or_create_sidecar(dir: &Path) -> Result<UserModelSidecar, String> {
    let path = dir.join(SIDECAR_FILE);
    if path.is_file() {
        let raw = fs::read_to_string(&path)
            .map_err(|error| format!("read sidecar {path:?} failed: {error}"))?;
        return serde_json::from_str(&raw)
            .map_err(|error| format!("parse sidecar {path:?} failed: {error}"));
    }
    let name = dir
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("Spine model")
        .to_string();
    let sidecar = UserModelSidecar {
        schema_version: 1,
        id: random_user_model_id(),
        name,
    };
    let raw = serde_json::to_string_pretty(&sidecar)
        .map_err(|error| format!("serialize sidecar failed: {error}"))?;
    fs::write(&path, raw).map_err(|error| format!("write sidecar {path:?} failed: {error}"))?;
    Ok(sidecar)
}

fn scan_model_dir(root: &Path, dir: &Path) -> Result<UserSpineCandidate, String> {
    let canonical_root = fs::canonicalize(root)
        .map_err(|error| format!("canonicalize root {root:?} failed: {error}"))?;
    let canonical_dir = fs::canonicalize(dir)
        .map_err(|error| format!("canonicalize model {dir:?} failed: {error}"))?;
    if !canonical_dir.starts_with(&canonical_root) {
        return Err("model directory escapes managed root".to_string());
    }
    let mut files = Vec::new();
    collect_model_files(&canonical_dir, &mut files)?;
    let mut pairs: BTreeMap<String, (Option<PathBuf>, Option<PathBuf>)> = BTreeMap::new();
    for path in &files {
        let extension = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("");
        if extension != "skel" && extension != "atlas" {
            continue;
        }
        let stem = path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_string();
        let pair = pairs.entry(stem).or_default();
        if extension == "skel" {
            pair.0 = Some(path.clone());
        } else {
            pair.1 = Some(path.clone());
        }
    }
    let complete = pairs
        .into_values()
        .filter_map(|(skeleton, atlas)| Some((skeleton?, atlas?)))
        .collect::<Vec<_>>();
    if complete.len() != 1 {
        return Err(format!(
            "expected exactly one same-name .skel/.atlas pair, found {}",
            complete.len()
        ));
    }
    let (skeleton, atlas) = complete.into_iter().next().expect("one complete pair");
    let version = read_spine_version(&skeleton)?;
    if !version.starts_with("3.8.") || version == "3.8.75" {
        return Err(format!(
            "unsupported Spine runtime version {version:?}; expected 3.8.x except 3.8.75"
        ));
    }
    let atlas_parent = atlas.parent().ok_or("atlas has no parent")?;
    let textures = atlas_texture_pages(&atlas)?
        .into_iter()
        .map(|page| atlas_parent.join(page))
        .collect::<Vec<_>>();
    for texture in &textures {
        let canonical = fs::canonicalize(texture)
            .map_err(|error| format!("missing atlas texture {texture:?}: {error}"))?;
        if !canonical.starts_with(&canonical_dir) || !canonical.is_file() {
            return Err(format!(
                "atlas texture escapes model directory: {texture:?}"
            ));
        }
    }
    let sidecar = load_or_create_sidecar(&canonical_dir)?;
    let mut revision_files = vec![skeleton.clone(), atlas.clone()];
    revision_files.extend(textures.iter().cloned());
    Ok(UserSpineCandidate {
        id: sidecar.id,
        name: sidecar.name,
        model_dir: path_string(canonical_dir),
        skeleton_path: path_string(skeleton),
        atlas_path: path_string(atlas),
        texture_paths: textures.into_iter().map(path_string).collect(),
        content_revision: content_revision(&revision_files)?,
        runtime_version: version,
    })
}

fn scan_user_models(state: &RwLock<UserModelState>) -> Result<UserModelScanSnapshot, String> {
    let root = ensure_user_model_root()?;
    let mut directories = fs::read_dir(&root)
        .map_err(|error| format!("read user model root {root:?} failed: {error}"))?
        .filter_map(Result::ok)
        .filter_map(|entry| entry.file_type().ok()?.is_dir().then_some(entry.path()))
        .collect::<Vec<_>>();
    directories.sort();
    let previous_paths = state
        .read()
        .map_err(|_| "avatar model state poisoned".to_string())?
        .validated
        .values()
        .map(|model| model.candidate.model_dir.clone())
        .collect::<Vec<_>>();
    directories.sort_by_key(|dir| !previous_paths.contains(&path_string(dir.clone())));

    let mut candidates = Vec::new();
    let mut diagnostics = Vec::new();
    let mut claimed_ids = BTreeMap::<String, String>::new();
    for dir in directories {
        match scan_model_dir(&root, &dir) {
            Ok(mut candidate) => {
                if let Some(owner) = claimed_ids.get(&candidate.id) {
                    let sidecar_path = dir.join(SIDECAR_FILE);
                    candidate.id = random_user_model_id();
                    let replacement = UserModelSidecar {
                        schema_version: 1,
                        id: candidate.id.clone(),
                        name: candidate.name.clone(),
                    };
                    let result = serde_json::to_string_pretty(&replacement)
                        .map_err(|error| error.to_string())
                        .and_then(|raw| {
                            fs::write(&sidecar_path, raw).map_err(|error| error.to_string())
                        });
                    if let Err(error) = result {
                        diagnostics.push(UserModelDiagnostic {
                            model_dir: path_string(dir),
                            code: "duplicate-model-id-read-only".to_string(),
                            message: format!("model id already belongs to {owner}; could not rewrite sidecar: {error}"),
                        });
                        continue;
                    }
                }
                claimed_ids.insert(candidate.id.clone(), candidate.model_dir.clone());
                candidates.push(candidate);
            }
            Err(message) => diagnostics.push(UserModelDiagnostic {
                model_dir: path_string(dir),
                code: "preflight-failed".to_string(),
                message,
            }),
        }
    }
    let snapshot = UserModelScanSnapshot {
        candidates,
        diagnostics,
    };
    let mut current = state
        .write()
        .map_err(|_| "avatar model state poisoned".to_string())?;
    current.validated.retain(|id, model| {
        snapshot.candidates.iter().any(|candidate| {
            candidate.id == *id && candidate.content_revision == model.candidate.content_revision
        })
    });
    current.scan = snapshot.clone();
    write_user_catalog(&current)?;
    Ok(snapshot)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ValidationResult {
    id: String,
    content_revision: String,
    success: bool,
    default_animation: Option<String>,
    error: Option<String>,
}

fn clear_runtime_validation_diagnostics(snapshot: &mut UserModelScanSnapshot, model_dir: &str) {
    snapshot.diagnostics.retain(|diagnostic| {
        diagnostic.code != "runtime-validation-failed" || diagnostic.model_dir != model_dir
    });
}

#[tauri::command]
pub fn get_user_avatar_model_scan(
    manager: State<'_, Arc<AvatarModelManager>>,
) -> Result<UserModelScanSnapshot, String> {
    manager
        .state
        .read()
        .map(|state| state.scan.clone())
        .map_err(|_| "avatar model state poisoned".to_string())
}

#[tauri::command]
pub fn rescan_user_avatar_models(app: AppHandle, manager: State<'_, Arc<AvatarModelManager>>) {
    manager.inner().request_scan(app);
}

#[tauri::command]
pub fn open_user_spine38_models_directory(app: AppHandle) -> Result<(), String> {
    let root = ensure_user_model_root()?;
    app.opener()
        .open_path(root.to_string_lossy(), None::<&str>)
        .map_err(|error| format!("open user model directory failed: {error}"))
}

#[tauri::command]
pub fn complete_user_avatar_model_validation(
    app: AppHandle,
    manager: State<'_, Arc<AvatarModelManager>>,
    result: ValidationResult,
) -> Result<(), String> {
    let mut state = manager
        .state
        .write()
        .map_err(|_| "avatar model state poisoned".to_string())?;
    let candidate = state
        .scan
        .candidates
        .iter()
        .find(|candidate| {
            candidate.id == result.id && candidate.content_revision == result.content_revision
        })
        .cloned()
        .ok_or("validation result is stale")?;
    clear_runtime_validation_diagnostics(&mut state.scan, &candidate.model_dir);
    if result.success {
        let default_animation = result
            .default_animation
            .filter(|value| !value.is_empty())
            .ok_or("successful validation must include a default animation")?;
        state.validated.insert(
            candidate.id.clone(),
            ValidatedUserModel {
                candidate,
                default_animation,
            },
        );
    } else {
        state.validated.remove(&result.id);
        if let Some(error) = result.error {
            state.scan.diagnostics.push(UserModelDiagnostic {
                model_dir: candidate.model_dir,
                code: "runtime-validation-failed".to_string(),
                message: error,
            });
        }
    }
    write_user_catalog(&state)?;
    app.emit(
        EVENT_CATALOG_CHANGED,
        serde_json::json!({ "id": result.id }),
    )
    .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn get_installed_avatar_model_resources(
    app: tauri::AppHandle,
    manager: State<'_, Arc<AvatarModelManager>>,
) -> Result<Vec<InstalledAvatarModelResources>, String> {
    // Configured webviews can request the catalog while Tauri setup is still installing the
    // bundled seed files. Waiting on the same install lock prevents a first-launch empty catalog
    // from being cached by the pet window until the next process restart.
    ensure_builtin_avatar_models_installed(&app)?;
    let mut resources = Vec::with_capacity(1 + ARK_SPINE_SLUGS.len());
    resources.push(live2d_resources());
    resources.extend(ARK_SPINE_SLUGS.iter().map(|slug| spine_resources(slug)));
    let state = manager
        .state
        .read()
        .map_err(|_| "avatar model state poisoned".to_string())?;
    resources.extend(
        state
            .validated
            .values()
            .map(|model| InstalledAvatarModelResources {
                id: model.candidate.id.clone(),
                backend: "spine".to_string(),
                resources: serde_json::json!({
                    "skeletonPath": model.candidate.skeleton_path,
                    "atlasPath": model.candidate.atlas_path,
                    "texturePaths": model.candidate.texture_paths,
                }),
                available: true,
                error: None,
                name: Some(model.candidate.name.clone()),
                content_revision: Some(model.candidate.content_revision.clone()),
                default_animation: Some(model.default_animation.clone()),
            }),
    );
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

    fn binary_string(value: &str) -> Vec<u8> {
        let mut length = value.len() as u32 + 1;
        let mut out = Vec::new();
        loop {
            let mut byte = (length & 0x7f) as u8;
            length >>= 7;
            if length != 0 {
                byte |= 0x80;
            }
            out.push(byte);
            if length == 0 {
                break;
            }
        }
        out.extend_from_slice(value.as_bytes());
        out
    }

    fn write_skeleton_header(path: &Path, version: &str) {
        let mut bytes = binary_string("hash");
        bytes.extend(binary_string(version));
        fs::write(path, bytes).expect("write skeleton");
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

    #[test]
    fn reads_spine_binary_version_header() {
        let dir = temp_dir("version");
        let skeleton = dir.join("model.skel");
        write_skeleton_header(&skeleton, "3.8.99");

        assert_eq!(read_spine_version(&skeleton).expect("version"), "3.8.99");
        fs::remove_dir_all(dir).expect("cleanup");
    }

    #[test]
    fn preflight_discovers_pair_textures_and_creates_stable_sidecar() {
        let root = temp_dir("preflight");
        let model = root.join("amiya");
        fs::create_dir_all(&model).expect("model dir");
        write_skeleton_header(&model.join("build_char.skel"), "3.8.99");
        fs::write(
            model.join("build_char.atlas"),
            "build_char.png\nsize: 64,64\nformat: RGBA8888\n\nbody\n  rotate: false\n",
        )
        .expect("atlas");
        fs::write(model.join("build_char.png"), b"png").expect("texture");

        let first = scan_model_dir(&root, &model).expect("first scan");
        let second = scan_model_dir(&root, &model).expect("second scan");

        assert_eq!(first.id, second.id);
        assert_eq!(first.content_revision, second.content_revision);
        assert_eq!(first.runtime_version, "3.8.99");
        assert_eq!(first.texture_paths.len(), 1);
        assert!(model.join(SIDECAR_FILE).is_file());
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn preflight_rejects_known_incompatible_3_8_75() {
        let root = temp_dir("unsupported");
        let model = root.join("bad");
        fs::create_dir_all(&model).expect("model dir");
        write_skeleton_header(&model.join("model.skel"), "3.8.75");
        fs::write(model.join("model.atlas"), "model.png\nsize: 1,1\n").expect("atlas");
        fs::write(model.join("model.png"), b"png").expect("texture");

        let error = scan_model_dir(&root, &model).expect_err("must reject 3.8.75");
        assert!(error.contains("3.8.75"));
        fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn successful_revalidation_can_clear_a_stale_runtime_diagnostic() {
        let mut snapshot = UserModelScanSnapshot {
            candidates: Vec::new(),
            diagnostics: vec![
                UserModelDiagnostic {
                    model_dir: "model-a".to_string(),
                    code: "runtime-validation-failed".to_string(),
                    message: "old runtime error".to_string(),
                },
                UserModelDiagnostic {
                    model_dir: "model-b".to_string(),
                    code: "preflight-failed".to_string(),
                    message: "keep me".to_string(),
                },
            ],
        };

        clear_runtime_validation_diagnostics(&mut snapshot, "model-a");

        assert_eq!(snapshot.diagnostics.len(), 1);
        assert_eq!(snapshot.diagnostics[0].model_dir, "model-b");
    }
}
