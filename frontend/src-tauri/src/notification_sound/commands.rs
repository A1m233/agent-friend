use super::{assets, NotificationSoundState};
use crate::{
    paths,
    settings::{self, NotificationSoundSource, Settings},
};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Manager, WebviewWindow};

pub(crate) fn check_window(label: &str) -> Result<(), String> {
    if label == "chat" {
        Ok(())
    } else {
        Err("提示音设置仅可在设置窗口操作".into())
    }
}

#[tauri::command]
pub async fn import_notification_sound(
    app: AppHandle,
    window: WebviewWindow,
) -> Result<Option<NotificationSoundSource>, String> {
    check_window(window.label())?;
    // Native selection occurs here; the command never accepts an arbitrary path from a WebView.
    let Some(file) = rfd::AsyncFileDialog::new()
        .set_title("选择提示音（10 MiB / 30 秒以内）")
        .add_filter("音频", &["mp3", "wav"])
        .pick_file()
        .await
    else {
        return Ok(None);
    };
    let path = file.path().to_path_buf();
    tauri::async_runtime::spawn_blocking(move || {
        let migration = app.state::<Arc<crate::data_migration::MigrationCoordinator>>();
        let _migration_guard = migration.sound_asset_update_guard()?;
        let sound = app.state::<NotificationSoundState>();
        let _guard = sound.operations.lock().map_err(|_| "提示音正在更新")?;
        let settings = app.state::<Arc<Mutex<Settings>>>();
        let root = paths::notification_sounds_dir();
        let source = assets::import(&root, &path)?;
        if let Err(error) = settings::save_notification_sound(&app, &settings, source.clone()) {
            if let Ok(current) = settings.lock() {
                assets::cleanup(&root, &current.notification_sound);
            }
            return Err(error);
        }
        assets::cleanup(&root, &source);
        Ok(Some(source))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn reset_notification_sound(
    app: AppHandle,
    window: WebviewWindow,
) -> Result<NotificationSoundSource, String> {
    check_window(window.label())?;
    tauri::async_runtime::spawn_blocking(move || {
        let migration = app.state::<Arc<crate::data_migration::MigrationCoordinator>>();
        let _migration_guard = migration.sound_asset_update_guard()?;
        let sound = app.state::<NotificationSoundState>();
        let _guard = sound.operations.lock().map_err(|_| "提示音正在更新")?;
        let settings = app.state::<Arc<Mutex<Settings>>>();
        let source = NotificationSoundSource::default();
        settings::save_notification_sound(&app, &settings, source.clone())?;
        assets::cleanup(&paths::notification_sounds_dir(), &source);
        Ok(source)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn preview_notification_sound(
    app: AppHandle,
    window: WebviewWindow,
    preview_id: String,
) -> Result<(), String> {
    check_window(window.label())?;
    if preview_id.is_empty() || preview_id.len() > 128 {
        return Err("试听标识无效".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let settings = app.state::<Arc<Mutex<Settings>>>();
        let source = settings
            .lock()
            .map_err(|_| "设置不可用")?
            .notification_sound
            .clone();
        app.state::<NotificationSoundState>()
            .player
            .play(source, Some(preview_id))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn stop_notification_sound_preview(
    app: AppHandle,
    window: WebviewWindow,
    preview_id: String,
) -> Result<(), String> {
    check_window(window.label())?;
    tauri::async_runtime::spawn_blocking(move || {
        app.state::<NotificationSoundState>()
            .player
            .stop_preview(preview_id)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn notification_sound_status(
    app: AppHandle,
    window: WebviewWindow,
) -> Result<Option<String>, String> {
    check_window(window.label())?;
    tauri::async_runtime::spawn_blocking(move || {
        let settings = app.state::<Arc<Mutex<Settings>>>();
        let source = settings
            .lock()
            .map_err(|_| "设置不可用")?
            .notification_sound
            .clone();
        Ok(match source {
            NotificationSoundSource::Default {} => None,
            NotificationSoundSource::Custom { asset_id, .. } => {
                assets::load(&paths::notification_sounds_dir(), &asset_id).err()
            }
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub fn notification_sound_preview_active(
    window: WebviewWindow,
    state: tauri::State<'_, NotificationSoundState>,
    preview_id: String,
) -> Result<bool, String> {
    check_window(window.label())?;
    Ok(state.player.preview_active(&preview_id))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_settings_surfaces_can_import_or_preview() {
        assert!(check_window("chat").is_ok());
        for window in ["bubble", "pet", "workspace", "voice-call", ""] {
            assert!(check_window(window).is_err());
        }
    }
}
