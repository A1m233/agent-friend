//! User-initiated file navigation. Files are revealed, never executed.
use std::path::{Path, PathBuf};
use tauri_plugin_opener::OpenerExt;

#[derive(Clone, Copy, Debug, serde::Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum TargetKind {
    File,
    Directory,
}

fn checked_path(path: &Path, expected_kind: TargetKind) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err("需要完整文件路径".into());
    }
    let metadata = path.metadata().map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => "文件或文件夹已不存在",
        std::io::ErrorKind::PermissionDenied => "没有访问此文件或文件夹的权限",
        _ => "无法访问此文件或文件夹",
    })?;
    let matches = match expected_kind {
        TargetKind::File => metadata.is_file(),
        TargetKind::Directory => metadata.is_dir(),
    };
    if !matches {
        return Err("目标类型已改变，请重新打开链接菜单".into());
    }
    Ok(path.to_path_buf())
}

#[tauri::command]
pub async fn reveal_link_target(
    app: tauri::AppHandle,
    path: String,
    expected_kind: TargetKind,
) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = checked_path(Path::new(&path), expected_kind)?;
        let opener = app.opener();
        let result = match expected_kind {
            TargetKind::File => opener.reveal_item_in_dir(path),
            TargetKind::Directory => opener.open_path(path.to_string_lossy(), None::<&str>),
        };
        result.map_err(|_| "无法打开文件夹，请复制路径后在文件管理器中查看".into())
    })
    .await
    .map_err(|_| "文件定位任务失败".to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_relative_missing_and_changed_targets() {
        assert!(checked_path(Path::new("relative"), TargetKind::File).is_err());
        let root = std::env::temp_dir().join(format!("link-navigation-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let file = root.join("file.txt");
        std::fs::write(&file, "test").unwrap();
        assert_eq!(checked_path(&file, TargetKind::File).unwrap(), file);
        assert!(checked_path(&file, TargetKind::Directory).is_err());
        assert!(checked_path(&root, TargetKind::File).is_err());
        std::fs::remove_file(&file).unwrap();
        assert!(checked_path(&file, TargetKind::File).is_err());
        std::fs::remove_dir(&root).unwrap();
    }
}
