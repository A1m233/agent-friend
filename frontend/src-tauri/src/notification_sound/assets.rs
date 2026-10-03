use crate::settings::NotificationSoundSource;
use rodio::{Decoder, Source};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Cursor, Read, Write},
    path::Path,
};

pub const MAX_BYTES: u64 = 10 * 1024 * 1024;
pub const MAX_SECONDS: u64 = 30;

pub struct Audio {
    pub samples: Vec<f32>,
    pub channels: u16,
    pub rate: u32,
}

pub fn valid_id(id: &str) -> bool {
    let Some((hash, extension)) = id.rsplit_once('.') else {
        return false;
    };
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && matches!(extension, "mp3" | "wav")
}

pub fn read_bounded(path: &Path) -> Result<Vec<u8>, String> {
    let file =
        fs::File::open(path).map_err(|_| "无法读取提示音文件，请重新选择音频".to_string())?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("请选择普通音频文件".into());
    }
    let mut bytes = Vec::new();
    file.take(MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err("音频不能超过 10 MiB".into());
    }
    Ok(bytes)
}

pub fn decode(bytes: Vec<u8>) -> Result<Audio, String> {
    if bytes.is_empty() || bytes.len() as u64 > MAX_BYTES {
        return Err("音频为空或超过 10 MiB".into());
    }
    let decoder = Decoder::new(Cursor::new(bytes))
        .map_err(|_| "音频无效或编码不受支持，请选择 MP3 或 WAV".to_string())?;
    let channels = decoder.channels();
    let rate = decoder.sample_rate();
    if channels == 0 || channels > 32 || rate == 0 || rate > 384_000 {
        return Err("音频采样参数不受支持".into());
    }
    let limit = u64::from(channels) * u64::from(rate) * MAX_SECONDS;
    // Stop decoding at the limit even when container metadata lies about duration.
    const MAX_SAMPLES: usize = 16 * 1024 * 1024;
    let samples: Vec<f32> = decoder
        .convert_samples()
        .take(((limit + 1) as usize).min(MAX_SAMPLES + 1))
        .collect();
    if samples.len() > MAX_SAMPLES {
        return Err("音频解码数据过大，请选择较低采样率的音频".into());
    }
    if samples.len() as u64 > limit {
        return Err("音频时长不能超过 30 秒".into());
    }
    if samples.is_empty() || samples.iter().any(|sample| !sample.is_finite()) {
        return Err("音频没有有效的可播放内容".into());
    }
    Ok(Audio {
        samples,
        channels,
        rate,
    })
}

pub fn load(root: &Path, id: &str) -> Result<Audio, String> {
    if !valid_id(id) {
        return Err("提示音标识无效，请重新选择音频".into());
    }
    let root = root
        .canonicalize()
        .map_err(|_| "自定义提示音已丢失，请重新选择音频".to_string())?;
    let path = root
        .join(id)
        .canonicalize()
        .map_err(|_| "自定义提示音已丢失，请重新选择音频".to_string())?;
    if path.parent() != Some(root.as_path()) {
        return Err("提示音路径不在受管目录内".into());
    }
    let bytes = read_bounded(&path)?;
    if format!("{:x}", Sha256::digest(&bytes)) != id[..64] {
        return Err("自定义提示音已损坏，请重新选择音频".into());
    }
    decode(bytes)
}

pub fn import(root: &Path, source: &Path) -> Result<NotificationSoundSource, String> {
    let extension = source
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !matches!(extension.as_str(), "mp3" | "wav") {
        return Err("仅支持 MP3、WAV 音频".into());
    }
    let bytes = read_bounded(source)?;
    decode(bytes.clone())?;
    let asset_id = format!("{:x}.{extension}", Sha256::digest(&bytes));
    fs::create_dir_all(root).map_err(|e| format!("无法保存提示音：{e}"))?;
    let path = root.join(&asset_id);
    // Never overwrite an existing content-addressed asset with different bytes.
    if !path.exists() || load(root, &asset_id).is_err() {
        let temp = root.join(format!("{:016x}.part", rand::random::<u64>()));
        let previous = root.join(format!("{:016x}.part", rand::random::<u64>()));
        let result = (|| {
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)?;
            file.write_all(&bytes)?;
            file.sync_all()?;
            drop(file);
            if path.exists() {
                fs::rename(&path, &previous)?;
            }
            if let Err(error) = fs::rename(&temp, &path) {
                if previous.exists() {
                    let _ = fs::rename(&previous, &path);
                }
                return Err(error);
            }
            let _ = fs::remove_file(&previous);
            Ok::<(), std::io::Error>(())
        })();
        if let Err(error) = result {
            let _ = fs::remove_file(temp);
            return Err(format!("无法保存提示音：{error}"));
        }
    }
    Ok(NotificationSoundSource::Custom {
        asset_id,
        name: source
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .chars()
            .filter(|ch| !ch.is_control())
            .take(200)
            .collect(),
    })
}

pub fn cleanup(root: &Path, keep: &NotificationSoundSource) {
    let keep_id = match keep {
        NotificationSoundSource::Custom { asset_id, .. } => Some(asset_id.as_str()),
        _ => None,
    };
    if let Ok(entries) = fs::read_dir(root) {
        for entry in entries.flatten() {
            let name = entry.file_name();
            if let Some(id) = name.to_str() {
                if valid_id(id) && Some(id) != keep_id {
                    let _ = fs::remove_file(entry.path());
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    pub fn wav(seconds: u32) -> Vec<u8> {
        let size = seconds * 8000 * 2;
        let mut data = Vec::new();
        data.extend(b"RIFF");
        data.extend((36 + size).to_le_bytes());
        data.extend(b"WAVEfmt ");
        data.extend(16u32.to_le_bytes());
        data.extend(1u16.to_le_bytes());
        data.extend(1u16.to_le_bytes());
        data.extend(8000u32.to_le_bytes());
        data.extend(16000u32.to_le_bytes());
        data.extend(2u16.to_le_bytes());
        data.extend(16u16.to_le_bytes());
        data.extend(b"data");
        data.extend(size.to_le_bytes());
        data.resize(44 + size as usize, 0);
        data
    }
    #[test]
    fn validates_content_and_duration_not_just_extension() {
        assert!(decode(vec![]).is_err());
        assert!(decode(b"not audio".to_vec()).is_err());
        assert!(decode(wav(0)).is_err());
        assert!(decode(wav(30)).is_ok());
        assert!(decode(wav(31)).err().unwrap().contains("30"));
    }
    #[test]
    fn ids_cannot_escape_asset_directory() {
        for id in [
            "../sound.wav",
            "C:\\sound.mp3",
            "/sound.wav",
            "default",
            "a.mp3",
        ] {
            assert!(!valid_id(id));
        }
        assert!(valid_id(&format!("{}.mp3", "a".repeat(64))));
    }

    #[test]
    fn decodes_generated_mp3_frames_and_bounds_file_reads() {
        // MPEG-1 Layer III, 128 kbit/s, 44.1 kHz, stereo. Zero side-info and
        // zero spectral payload encode silence; no third-party audio fixture.
        let mut frame = vec![0; 417];
        frame[..4].copy_from_slice(&[0xff, 0xfb, 0x90, 0x00]);
        let decoded = decode(frame.repeat(4)).expect("valid MP3 silence");
        assert_eq!(decoded.rate, 44_100);
        assert!(!decoded.samples.is_empty());

        let root = std::env::temp_dir().join(format!("af-sound-bound-{}", rand::random::<u64>()));
        fs::create_dir_all(&root).unwrap();
        let source = root.join("large.wav");
        fs::File::create(&source)
            .unwrap()
            .set_len(MAX_BYTES + 1)
            .unwrap();
        assert!(read_bounded(&source).err().unwrap().contains("10 MiB"));
        let managed = root.join("managed");
        assert!(import(&managed, &source).is_err());
        assert!(!managed.exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn import_survives_original_deletion_and_rejects_tampering() {
        let dir = std::env::temp_dir().join(format!("af-sound-{}", rand::random::<u64>()));
        fs::create_dir_all(&dir).unwrap();
        let source = dir.join("提醒.WAV");
        let managed = dir.join("managed");
        fs::write(&source, wav(1)).unwrap();
        let selection = import(&managed, &source).unwrap();
        fs::remove_file(&source).unwrap();
        let NotificationSoundSource::Custom { asset_id, name } = &selection else {
            panic!()
        };
        assert_eq!(name, "提醒.WAV");
        assert!(load(&managed, asset_id).is_ok());
        fs::write(managed.join(asset_id), b"broken").unwrap();
        assert!(load(&managed, asset_id).is_err());
        fs::write(&source, wav(1)).unwrap();
        assert_eq!(import(&managed, &source).unwrap(), selection);
        assert!(load(&managed, asset_id).is_ok());
        cleanup(&managed, &NotificationSoundSource::default());
        assert!(!managed.join(asset_id).exists());
        fs::remove_dir_all(dir).unwrap();
    }
}
