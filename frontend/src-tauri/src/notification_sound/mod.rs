//! Native, at-most-once sound for a live notification arrival.
//!
//! The notification ledger owns durable playback qualification. This state also
//! blocks duplicate push signals within one desktop process. UI visibility and
//! WebView lifetimes do not participate. Muted or failed playback is not retried.

use std::{
    collections::HashSet,
    sync::{Arc, Mutex},
};
use tauri::{AppHandle, Manager};

use crate::push_subscriber::PushEnvelope;

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
    fn consume(&self, notification_id: &str) -> Result<bool, String> {
        if notification_id.is_empty()
            || notification_id.len() > 256
            || notification_id
                .chars()
                .any(|ch| ch.is_whitespace() || ch.is_control())
        {
            return Err("invalid notification id".to_string());
        }
        Ok(self
            .consumed
            .lock()
            .map_err(|error| format!("notification sound state poisoned: {error}"))?
            .insert(notification_id.to_string()))
    }

    fn play_once(
        &self,
        notification_id: &str,
        settings: &Settings,
        play: impl FnOnce(NotificationSoundSource) -> bool,
    ) -> Result<(), String> {
        if !self.consume(notification_id)? {
            return Ok(());
        }
        let source = match &settings.notification_sound {
            NotificationSoundSource::Default {} => "default",
            NotificationSoundSource::Custom { .. } => "custom",
        };
        let outcome = if !settings.notification_sound_enabled {
            "disabled"
        } else if play(settings.notification_sound.clone()) {
            "played"
        } else {
            "unavailable"
        };
        log::info!("notification sound id={notification_id} source={source} outcome={outcome}");
        Ok(())
    }
}

/// Called by the native SSE subscriber before any WebView projection.
pub(crate) fn handle_push(app: &AppHandle, envelope: &PushEnvelope) {
    if let Some(id) = terminal_id(envelope) {
        // A terminal revision may overtake an arrival on the publishing threads.
        // Remember it natively so a delayed signal cannot revive its sound.
        if let Err(error) = app.state::<NotificationSoundState>().consume(id) {
            log::warn!("notification sound invalidation unavailable: {error}");
        }
        return;
    }
    let Some(id) = arrival_id(envelope) else {
        return;
    };
    let settings = app.state::<Arc<Mutex<Settings>>>();
    let current = match settings.lock() {
        Ok(value) => value.clone(),
        Err(error) => {
            log::warn!("notification sound settings unavailable: {error}");
            return;
        }
    };
    let app = app.clone();
    let id = id.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<NotificationSoundState>();
        if let Err(error) = state.play_once(&id, &current, |source| {
            state.player.play(source, None).is_ok()
        }) {
            log::warn!("notification sound unavailable: {error}");
        }
    });
}

fn terminal_id(envelope: &PushEnvelope) -> Option<&str> {
    if envelope.kind != "notification_changed" {
        return None;
    }
    let item = envelope.notification.as_ref()?;
    if matches!(
        item.get("delivery")?.get("status")?.as_str()?,
        "dismissed" | "invalidated"
    ) {
        return item.get("notificationId")?.as_str();
    }
    None
}

fn arrival_id(envelope: &PushEnvelope) -> Option<&str> {
    if envelope.kind != "notification_changed" || !envelope.notification_sound {
        return None;
    }
    let item = envelope.notification.as_ref()?;
    if item.get("generationStatus")?.as_str()? != "ready"
        || !matches!(
            item.get("delivery")?.get("status")?.as_str()?,
            "pending" | "prepared" | "presented"
        )
    {
        return None;
    }
    item.get("notificationId")?.as_str()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn only_explicit_live_ready_arrivals_play_without_any_window() {
        let value = serde_json::json!({
            "kind": "notification_changed", "session_id": "s", "seq": 1,
            "source_kind": null, "events": [], "notification_sound": true,
            "notification": {"notificationId": "new", "generationStatus": "ready",
                "delivery": {"status": "pending"}}
        });
        let envelope: PushEnvelope = serde_json::from_value(value.clone()).unwrap();
        let state = NotificationSoundState::default();
        let mut played = 0;
        for _ in 0..2 {
            state
                .play_once(arrival_id(&envelope).unwrap(), &Settings::default(), |_| {
                    played += 1;
                    true
                })
                .unwrap();
        }
        assert_eq!(played, 1);
        for status in ["pending", "prepared", "presented"] {
            let mut changed = value.clone();
            changed["notification"]["delivery"]["status"] = status.into();
            assert_eq!(
                arrival_id(&serde_json::from_value(changed).unwrap()),
                Some("new")
            );
        }
        for status in ["dismissed", "invalidated"] {
            let mut changed = value.clone();
            changed["notification"]["delivery"]["status"] = status.into();
            changed["notification_sound"] = false.into();
            let terminal: PushEnvelope = serde_json::from_value(changed).unwrap();
            assert!(arrival_id(&terminal).is_none());
            let silent = NotificationSoundState::default();
            silent.consume(terminal_id(&terminal).unwrap()).unwrap();
            silent
                .play_once(arrival_id(&envelope).unwrap(), &Settings::default(), |_| {
                    panic!("an older arrival cannot sound after dismissal or invalidation")
                })
                .unwrap();
        }
        for status in ["pending", "generating", "suppressed", "expired"] {
            let mut changed = value.clone();
            changed["notification"]["generationStatus"] = status.into();
            assert!(arrival_id(&serde_json::from_value(changed).unwrap()).is_none());
        }
        let mut legacy = value.clone();
        legacy.as_object_mut().unwrap().remove("notification_sound");
        assert!(arrival_id(&serde_json::from_value(legacy).unwrap()).is_none());
        let mut silent = value.clone();
        silent["notification_sound"] = false.into();
        assert!(arrival_id(&serde_json::from_value(silent).unwrap()).is_none());
        let mut heartbeat = value;
        heartbeat["kind"] = "heartbeat".into();
        assert!(arrival_id(&serde_json::from_value(heartbeat).unwrap()).is_none());
    }

    #[test]
    fn duplicate_arrivals_play_only_once() {
        let state = NotificationSoundState::default();
        let settings = Settings::default();
        let played = AtomicUsize::new(0);
        for _ in 0..3 {
            state
                .play_once("n1", &settings, |source| {
                    assert_eq!(source, NotificationSoundSource::default());
                    played.fetch_add(1, Ordering::SeqCst);
                    true
                })
                .expect("play request");
        }
        assert_eq!(played.load(Ordering::SeqCst), 1);
        state
            .play_once("n2", &settings, |_| {
                played.fetch_add(1, Ordering::SeqCst);
                true
            })
            .expect("new notification");
        assert_eq!(played.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn disabled_or_failed_sound_is_not_replayed_later() {
        for case in ["disabled", "failed"] {
            let state = NotificationSoundState::default();
            let settings = Settings {
                notification_sound_enabled: case != "disabled",
                ..Settings::default()
            };
            let mut called = false;
            state
                .play_once("n1", &settings, |_| {
                    called = true;
                    false
                })
                .expect("first request");
            assert_eq!(called, case == "failed");
            state
                .play_once("n1", &Settings::default(), |_| panic!("must not replay"))
                .expect("duplicate");
        }
    }

    #[test]
    fn invalid_identity_never_plays() {
        let state = NotificationSoundState::default();
        for id in ["", " ", "a\nb", "a\0b"] {
            assert!(state
                .play_once(id, &Settings::default(), |_| panic!("must not play"))
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
                        .play_once("n1", &Settings::default(), |_| {
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
