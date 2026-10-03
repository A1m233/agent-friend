//! Native, at-most-once sound for a bubble's confirmed presentation.
//!
//! The notification ledger owns durable playback qualification. This state also
//! blocks repeated WebView invocations within one desktop process, including
//! window reloads. Sound failure or a disabled setting consumes the qualification.

use std::{
    collections::HashSet,
    sync::{Arc, Mutex},
};
use tauri::{AppHandle, Manager, WebviewWindow};

pub(crate) mod assets;
mod commands;
mod player;
pub use commands::*;

use crate::settings::{NotificationSoundSource, Settings};

#[derive(Default)]
pub struct NotificationSoundState {
    consumed: Mutex<HashSet<String>>,
    operations: Mutex<()>,
    player: player::Player,
}

impl NotificationSoundState {
    fn play_once(
        &self,
        window_label: &str,
        window_visible: bool,
        notification_id: &str,
        settings: &Settings,
        play: impl FnOnce(NotificationSoundSource) -> bool,
    ) -> Result<(), String> {
        if window_label != "bubble" {
            return Err("notification sound is only available to the bubble window".to_string());
        }
        if notification_id.is_empty()
            || notification_id.len() > 256
            || notification_id
                .chars()
                .any(|ch| ch.is_whitespace() || ch.is_control())
        {
            return Err("invalid notification id".to_string());
        }
        if !self
            .consumed
            .lock()
            .map_err(|error| format!("notification sound state poisoned: {error}"))?
            .insert(notification_id.to_string())
        {
            return Ok(());
        }
        let source = match &settings.notification_sound {
            NotificationSoundSource::Default {} => "default",
            NotificationSoundSource::Custom { .. } => "custom",
        };
        let outcome = if !settings.notification_sound_enabled {
            "disabled"
        } else if !window_visible {
            "hidden"
        } else if play(settings.notification_sound.clone()) {
            "played"
        } else {
            "unavailable"
        };
        log::info!("notification sound id={notification_id} source={source} outcome={outcome}");
        Ok(())
    }
}

#[tauri::command]
pub async fn play_notification_sound(
    app: AppHandle,
    window: WebviewWindow,
    settings: tauri::State<'_, Arc<Mutex<Settings>>>,
    notification_id: String,
) -> Result<(), String> {
    let current = settings
        .lock()
        .map_err(|error| format!("settings state poisoned: {error}"))?
        .clone();
    let label = window.label().to_string();
    let visible = window.is_visible().map_err(|error| error.to_string())?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<NotificationSoundState>();
        state.play_once(&label, visible, &notification_id, &current, |source| {
            state.player.play(source, None).is_ok()
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(target_os = "windows")]
fn play_default() -> bool {
    use windows::{
        core::w,
        Win32::Media::Audio::{PlaySoundW, SND_ALIAS, SND_ASYNC, SND_NODEFAULT, SND_SYSTEM},
    };
    unsafe {
        // A registry event alias respects the current Windows sound scheme.
        // SYSTEM selects system-sounds volume; NODEFAULT leaves missing/muted
        // events silent. The static UTF-16 alias outlives asynchronous playback.
        PlaySoundW(
            w!("Notification.Default"),
            None,
            SND_ALIAS | SND_ASYNC | SND_SYSTEM | SND_NODEFAULT,
        )
        .as_bool()
    }
}

#[cfg(not(target_os = "windows"))]
fn play_default() -> bool {
    // Other platform sound adapters can be added without changing notifications.
    false
}

#[cfg(target_os = "windows")]
fn stop_default() {
    use windows::Win32::Media::Audio::{PlaySoundW, SND_ASYNC};
    unsafe {
        let _ = PlaySoundW(None, None, SND_ASYNC);
    }
}

#[cfg(not(target_os = "windows"))]
fn stop_default() {}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn repeated_bubble_callbacks_play_only_once() {
        let state = NotificationSoundState::default();
        let settings = Settings::default();
        let played = AtomicUsize::new(0);
        for _ in 0..3 {
            state
                .play_once("bubble", true, "n1", &settings, |source| {
                    assert_eq!(source, NotificationSoundSource::default());
                    played.fetch_add(1, Ordering::SeqCst);
                    true
                })
                .expect("play request");
        }
        assert_eq!(played.load(Ordering::SeqCst), 1);
        state
            .play_once("bubble", true, "n2", &settings, |_| {
                played.fetch_add(1, Ordering::SeqCst);
                true
            })
            .expect("new notification");
        assert_eq!(played.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn forbidden_window_cannot_play_or_consume_bubble_qualification() {
        let state = NotificationSoundState::default();
        let settings = Settings::default();
        assert!(state
            .play_once("workspace", true, "n1", &settings, |_| panic!(
                "must not play"
            ))
            .is_err());
        let mut called = false;
        state
            .play_once("bubble", true, "n1", &settings, |_| {
                called = true;
                true
            })
            .expect("bubble");
        assert!(called);
    }

    #[test]
    fn disabled_hidden_or_failed_sound_is_not_replayed_later() {
        for case in ["disabled", "hidden", "failed"] {
            let state = NotificationSoundState::default();
            let settings = Settings {
                notification_sound_enabled: case != "disabled",
                ..Settings::default()
            };
            let mut called = false;
            state
                .play_once("bubble", case != "hidden", "n1", &settings, |_| {
                    called = true;
                    false
                })
                .expect("first request");
            assert_eq!(called, case == "failed");
            state
                .play_once("bubble", true, "n1", &Settings::default(), |_| {
                    panic!("must not replay")
                })
                .expect("duplicate");
        }
    }

    #[test]
    fn invalid_identity_never_plays() {
        let state = NotificationSoundState::default();
        for id in ["", " ", "a\nb", "a\0b"] {
            assert!(state
                .play_once("bubble", true, id, &Settings::default(), |_| panic!(
                    "must not play"
                ))
                .is_err());
        }
    }

    #[test]
    fn concurrent_requests_consume_one_qualification() {
        let state = NotificationSoundState::default();
        let played = AtomicUsize::new(0);
        std::thread::scope(|scope| {
            for _ in 0..8 {
                scope.spawn(|| {
                    state
                        .play_once("bubble", true, "n1", &Settings::default(), |_| {
                            played.fetch_add(1, Ordering::SeqCst);
                            true
                        })
                        .expect("request");
                });
            }
        });
        assert_eq!(played.load(Ordering::SeqCst), 1);
    }
}
