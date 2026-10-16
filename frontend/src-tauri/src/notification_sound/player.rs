use super::assets;
use crate::{paths, settings::NotificationSoundSource};
use rodio::{buffer::SamplesBuffer, OutputStream, Sink};
use std::{
    path::Path,
    sync::{mpsc, Arc, Mutex},
    time::{Duration, Instant},
};

type Reply = mpsc::SyncSender<Result<(), String>>;
enum Request {
    Play(NotificationSoundSource, Option<String>, Reply),
    Stop(String, Reply),
}

#[derive(Default)]
pub struct Player {
    sender: Mutex<Option<mpsc::SyncSender<Request>>>,
    active_preview: Arc<Mutex<Option<String>>>,
}

impl Player {
    fn send(
        &self,
        request: Request,
        reply: mpsc::Receiver<Result<(), String>>,
    ) -> Result<(), String> {
        let mut sender = self.sender.lock().map_err(|_| "播放器状态不可用")?;
        if sender.is_none() {
            // Bounded commands, no unbounded queue of stale notification sounds.
            let (tx, rx) = mpsc::sync_channel(8);
            let active = self.active_preview.clone();
            std::thread::Builder::new()
                .name("notification-audio".into())
                .spawn(move || run(rx, active))
                .map_err(|_| "无法启动提示音播放器")?;
            *sender = Some(tx);
        }
        sender
            .as_ref()
            .unwrap()
            .try_send(request)
            .map_err(|_| "提示音播放器正忙，请稍后重试")?;
        drop(sender);
        reply.recv().map_err(|_| "提示音播放器已停止".to_string())?
    }
    pub fn play(
        &self,
        source: NotificationSoundSource,
        preview: Option<String>,
    ) -> Result<(), String> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.send(Request::Play(source, preview, tx), rx)
    }
    pub fn preview_active(&self, id: &str) -> bool {
        self.active_preview
            .lock()
            .is_ok_and(|value| owns_preview(&value, id))
    }
    pub fn stop_preview(&self, id: String) -> Result<(), String> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.send(Request::Stop(id, tx), rx)
    }
}

fn owns_preview(active: &Option<String>, requested: &str) -> bool {
    active.as_deref() == Some(requested)
}

fn run(rx: mpsc::Receiver<Request>, active: Arc<Mutex<Option<String>>>) {
    // CPAL streams stay on this thread for their entire lifetime.
    let mut audio: Option<(OutputStream, Sink)> = None;
    let mut preview = None;
    let mut deadline = None;
    loop {
        let request = match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(request) => request,
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if audio.as_ref().is_some_and(|(_, sink)| sink.empty())
                    || deadline.is_some_and(|end| Instant::now() >= end)
                {
                    audio = None;
                    preview = None;
                    deadline = None;
                    if let Ok(mut value) = active.lock() {
                        *value = None;
                    }
                }
                continue;
            }
        };
        match request {
            Request::Stop(id, reply) => {
                if owns_preview(&preview, &id) {
                    audio = None;
                    preview = None;
                    deadline = None;
                    if let Ok(mut value) = active.lock() {
                        *value = None;
                    }
                }
                let _ = reply.send(Ok(()));
            }
            Request::Play(source, owner, reply) => {
                audio = None;
                preview = owner;
                deadline = Some(Instant::now() + Duration::from_secs(assets::MAX_SECONDS));
                let result =
                    load_audio(source, &paths::notification_sounds_dir(), preview.is_some())
                        .and_then(|data| {
                            let (stream, handle) = OutputStream::try_default()
                                .map_err(|_| "无法打开音频输出设备".to_string())?;
                            let sink = Sink::try_new(&handle)
                                .map_err(|_| "无法启动音频播放".to_string())?;
                            sink.append(SamplesBuffer::new(data.channels, data.rate, data.samples));
                            audio = Some((stream, sink));
                            Ok(())
                        });
                if result.is_err() {
                    preview = None;
                    deadline = None;
                    if let Ok(mut value) = active.lock() {
                        *value = None;
                    }
                }
                if let Ok(mut value) = active.lock() {
                    *value = preview.clone();
                }
                let _ = reply.send(result);
            }
        }
    }
}

fn load_audio(
    source: NotificationSoundSource,
    root: &Path,
    preview: bool,
) -> Result<assets::Audio, String> {
    match source {
        NotificationSoundSource::Default {} => assets::decode_default(),
        NotificationSoundSource::Custom { asset_id, .. } => match assets::load(root, &asset_id) {
            Ok(data) => Ok(data),
            Err(error) => {
                log::warn!("custom notification sound unavailable: {error}");
                if preview {
                    Err(error)
                } else {
                    assets::decode_default()
                }
            }
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_and_missing_custom_notification_use_bundled_audio() {
        let expected = assets::decode_default().unwrap();
        assert!(expected.samples.iter().any(|sample| sample.abs() > 0.0));
        let missing = NotificationSoundSource::Custom {
            asset_id: "invalid".into(),
            name: "missing.wav".into(),
        };
        for (source, preview) in [
            (NotificationSoundSource::default(), false),
            (NotificationSoundSource::default(), true),
            (missing.clone(), false),
        ] {
            let data = load_audio(source, Path::new("."), preview).unwrap();
            assert_eq!(data.channels, expected.channels);
            assert_eq!(data.rate, expected.rate);
            assert_eq!(data.samples, expected.samples);
        }
        assert!(load_audio(missing, Path::new("."), true).is_err());
    }

    #[test]
    fn stopping_old_preview_never_stops_notification_or_new_preview() {
        assert!(!owns_preview(&None, "old"));
        assert!(!owns_preview(&Some("new".into()), "old"));
        assert!(owns_preview(&Some("old".into()), "old"));
    }
}
