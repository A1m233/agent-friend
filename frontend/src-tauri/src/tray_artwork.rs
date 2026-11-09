use std::{io, time::Duration};

use tauri::{image::Image, tray::TrayIcon};

pub(crate) struct Asset {
    size: u32,
    png: &'static [u8],
}

macro_rules! asset {
    ($size:literal, $path:literal) => {
        Asset {
            size: $size,
            png: include_bytes!($path),
        }
    };
}

const ASSETS: &[Asset] = &[
    asset!(16, "../icons/tray/af-agent-tray-16.png"),
    asset!(20, "../icons/tray/af-agent-tray-20.png"),
    asset!(24, "../icons/tray/af-agent-tray-24.png"),
    asset!(32, "../icons/tray/af-agent-tray-32.png"),
    // Larger display scales use the existing app artwork from the same master.
    asset!(128, "../icons/128x128.png"),
    asset!(256, "../icons/128x128@2x.png"),
];

fn for_pixels(pixels: u32) -> &'static Asset {
    ASSETS
        .iter()
        .find(|asset| asset.size >= pixels)
        .unwrap_or(&ASSETS[ASSETS.len() - 1])
}

impl Asset {
    pub(crate) fn image(&self) -> Result<Image<'static>, tauri::Error> {
        let mut reader = png::Decoder::new(self.png)
            .read_info()
            .map_err(io::Error::other)?;
        let mut pixels = vec![0; reader.output_buffer_size()];
        let info = reader.next_frame(&mut pixels).map_err(io::Error::other)?;
        if info.color_type != png::ColorType::Rgba
            || info.bit_depth != png::BitDepth::Eight
            || (info.width, info.height) != (self.size, self.size)
        {
            return Err(tauri::Error::InvalidIcon(io::Error::new(
                io::ErrorKind::InvalidData,
                "expected a square RGBA8 tray icon",
            )));
        }
        Ok(Image::new_owned(pixels, info.width, info.height))
    }
}

#[cfg(target_os = "windows")]
pub(crate) fn initial() -> &'static Asset {
    use windows::{
        core::w,
        Win32::UI::{
            HiDpi::{GetDpiForWindow, GetSystemMetricsForDpi},
            WindowsAndMessaging::{FindWindowW, SM_CXSMICON},
        },
    };
    // The shell taskbar can be on a different display from our pet or chat window.
    let pixels = unsafe {
        let dpi = FindWindowW(w!("Shell_TrayWnd"), None)
            .map(|window| GetDpiForWindow(window))
            .unwrap_or(0);
        GetSystemMetricsForDpi(SM_CXSMICON, if dpi == 0 { 96 } else { dpi })
    };
    for_pixels(pixels.max(16) as u32)
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn initial() -> &'static Asset {
    for_pixels(32)
}

#[cfg(target_os = "windows")]
fn current(_tray: &TrayIcon) -> Option<&'static Asset> {
    Some(initial())
}

#[cfg(target_os = "macos")]
fn current(tray: &TrayIcon) -> Option<&'static Asset> {
    use objc2_current::MainThreadMarker;

    tray.with_inner_tray_icon(|inner| {
        let item = inner.ns_status_item()?;
        let button = item.button(MainThreadMarker::new()?)?;
        let scale = button.window()?.backingScaleFactor();
        // tray-icon sizes an NSStatusBarButton image to 18 points.
        Some(for_pixels((18.0 * scale).ceil() as u32))
    })
    .ok()
    .flatten()
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn current(tray: &TrayIcon) -> Option<&'static Asset> {
    use tauri::Manager;
    let window = tray.app_handle().get_webview_window("pet")?;
    let pixels = (22.0 * window.scale_factor().ok()?).ceil() as u32;
    Some(for_pixels(pixels))
}

pub(crate) struct Watch(tauri::async_runtime::JoinHandle<()>);

impl Watch {
    pub(crate) fn stop(&self) {
        self.0.abort();
    }
}

pub(crate) fn watch(tray: TrayIcon, mut applied: &'static Asset) -> Watch {
    // The taskbar can change display scale while every app window is hidden.
    Watch(tauri::async_runtime::spawn(async move {
        loop {
            if let Some(next) = current(&tray).filter(|next| next.size != applied.size) {
                match next.image().and_then(|image| tray.set_icon(Some(image))) {
                    Ok(()) => applied = next,
                    Err(error) => log::warn!("could not update tray icon: {error}"),
                }
            }
            tokio::time::sleep(Duration::from_secs(3)).await;
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selects_native_tray_sizes_and_larger_app_artwork_for_high_dpi() {
        for (requested, expected) in [
            (16, 16),
            (20, 20),
            (24, 24),
            (28, 32),
            (32, 32),
            (36, 128),
            (512, 256),
        ] {
            assert_eq!(for_pixels(requested).size, expected);
        }
    }

    #[test]
    fn all_embedded_frames_have_the_expected_size_and_transparency() {
        for asset in ASSETS {
            let image = asset.image().unwrap();
            assert_eq!((image.width(), image.height()), (asset.size, asset.size));
            assert!(image.rgba().chunks_exact(4).any(|p| p[3] == 0));
            assert!(image.rgba().chunks_exact(4).any(|p| p[3] == 255));
        }
    }
}
