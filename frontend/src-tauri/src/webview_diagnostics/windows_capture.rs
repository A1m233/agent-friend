use std::{
    ffi::c_void,
    fs,
    io::Cursor,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

use chrono::Local;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager, WebviewWindow, Wry};
use webview2_com::{
    CapturePreviewCompletedHandler,
    Microsoft::Web::WebView2::Win32::{
        ICoreWebView2, COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG,
    },
};
use windows::{
    core::Interface,
    Win32::{
        Foundation::HGLOBAL,
        Graphics::{
            Direct3D11::ID3D11Device,
            Dwm::DwmIsCompositionEnabled,
            Dxgi::{
                CreateDXGIFactory1, IDXGIAdapter1, IDXGIFactory1, DXGI_ADAPTER_FLAG_SOFTWARE,
                DXGI_ERROR_NOT_FOUND,
            },
            Gdi::{
                BitBlt, CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GetDC,
                ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, CAPTUREBLT,
                DIB_RGB_COLORS, HGDIOBJ, ROP_CODE, SRCCOPY,
            },
        },
        System::Com::{
            StructuredStorage::CreateStreamOnHGlobal, STATFLAG_NONAME, STATSTG, STREAM_SEEK_SET,
        },
        UI::WindowsAndMessaging::{
            GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_REMOTESESSION,
            SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
        },
    },
};

use crate::shell_diagnostics;

use super::{
    RenderEvidenceRecord, RenderImageEvidence, RenderPixelMetrics, RENDER_EVIDENCE_DESKTOP_FILE,
    RENDER_EVIDENCE_LATEST_DIR, RENDER_EVIDENCE_METADATA_FILE, RENDER_EVIDENCE_SCHEMA_VERSION,
    RENDER_EVIDENCE_WEBVIEW_FILE,
};

const CAPTURE_TIMEOUT_SECONDS: u64 = 10;
const MAX_IMAGE_BYTES: usize = 48 * 1024 * 1024;
const MAX_DECODED_IMAGE_BYTES: usize = MAX_IMAGE_BYTES * 4;

struct CapturedImage {
    bytes: Vec<u8>,
    started_unix_ms: u64,
    completed_unix_ms: u64,
    width: u32,
    height: u32,
    pixel_metrics: RenderPixelMetrics,
}

pub(super) fn capture_graphics_snapshot() -> Value {
    let mut errors = Vec::new();
    let dwm_composition_enabled = match unsafe { DwmIsCompositionEnabled() } {
        Ok(enabled) => Some(enabled.as_bool()),
        Err(error) => {
            errors.push(format!("DwmIsCompositionEnabled failed: {error}"));
            None
        }
    };
    let adapters = match enumerate_graphics_adapters() {
        Ok(adapters) => adapters,
        Err(error) => {
            errors.push(error);
            Vec::new()
        }
    };
    json!({
        "supported": true,
        "capturedAt": Local::now().to_rfc3339(),
        "dwmCompositionEnabled": dwm_composition_enabled,
        "remoteSession": unsafe { GetSystemMetrics(SM_REMOTESESSION) } != 0,
        "adapters": adapters,
        "errors": errors,
    })
}

fn enumerate_graphics_adapters() -> Result<Vec<Value>, String> {
    let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1() }
        .map_err(|error| format!("create DXGI factory failed: {error}"))?;
    let mut adapters = Vec::new();
    for index in 0_u32..32 {
        let adapter: IDXGIAdapter1 = match unsafe { factory.EnumAdapters1(index) } {
            Ok(adapter) => adapter,
            Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(error) => return Err(format!("enumerate DXGI adapter {index} failed: {error}")),
        };
        let description = unsafe { adapter.GetDesc1() }
            .map_err(|error| format!("read DXGI adapter {index} failed: {error}"))?;
        let name_length = description
            .Description
            .iter()
            .position(|value| *value == 0)
            .unwrap_or(description.Description.len());
        let driver_version = unsafe { adapter.CheckInterfaceSupport(&ID3D11Device::IID) }
            .ok()
            .map(format_driver_version);
        adapters.push(json!({
            "index": index,
            "description": String::from_utf16_lossy(&description.Description[..name_length]),
            "vendorId": format!("0x{:04x}", description.VendorId),
            "deviceId": format!("0x{:04x}", description.DeviceId),
            "subsystemId": format!("0x{:08x}", description.SubSysId),
            "revision": description.Revision,
            "dedicatedVideoMemoryBytes": description.DedicatedVideoMemory,
            "dedicatedSystemMemoryBytes": description.DedicatedSystemMemory,
            "sharedSystemMemoryBytes": description.SharedSystemMemory,
            "software": description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE.0 as u32 != 0,
            "adapterLuid": format!(
                "{:08x}:{:08x}",
                description.AdapterLuid.HighPart as u32,
                description.AdapterLuid.LowPart
            ),
            "userModeDriverVersion": driver_version,
        }));
    }
    Ok(adapters)
}

fn format_driver_version(version: i64) -> String {
    let version = version as u64;
    format!(
        "{}.{}.{}.{}",
        (version >> 48) & 0xffff,
        (version >> 32) & 0xffff,
        (version >> 16) & 0xffff,
        version & 0xffff
    )
}

pub(super) async fn capture_and_store(
    app: &AppHandle,
    evidence_root: &Path,
    trigger: &str,
    hidden_duration_ms: Option<u64>,
) -> Result<RenderEvidenceRecord, String> {
    let window = app
        .get_webview_window("pet")
        .ok_or_else(|| "pet WebView window is unavailable".to_string())?;
    let capture_id = format!("{}-{}", unix_time_ms(), std::process::id());
    let capture_started_at = Local::now().to_rfc3339();
    let target_window = target_window_snapshot(&window);

    let webview_future = capture_webview_preview(&window);
    let desktop_task = tauri::async_runtime::spawn_blocking(capture_presented_desktop);
    let (webview_result, desktop_result) = tokio::join!(webview_future, desktop_task);
    let graphics = capture_graphics_snapshot();
    let windows_shell_snapshot = shell_diagnostics::capture_current_snapshot(app)
        .unwrap_or_else(|error| json!({"captureError": error}));
    let desktop_result = desktop_result
        .map_err(|error| format!("desktop capture worker failed: {error}"))
        .and_then(|result| result);

    let mut errors = Vec::new();
    let webview2 = match webview_result {
        Ok(image) => match write_captured_image(
            evidence_root,
            &capture_id,
            RENDER_EVIDENCE_WEBVIEW_FILE,
            image,
        ) {
            Ok(image) => Some(image),
            Err(error) => {
                errors.push(format!("persist WebView2 CapturePreview failed: {error}"));
                None
            }
        },
        Err(error) => {
            errors.push(format!("WebView2 CapturePreview failed: {error}"));
            None
        }
    };
    let desktop = match desktop_result {
        Ok(image) => match write_captured_image(
            evidence_root,
            &capture_id,
            RENDER_EVIDENCE_DESKTOP_FILE,
            image,
        ) {
            Ok(image) => Some(image),
            Err(error) => {
                errors.push(format!("persist Windows desktop capture failed: {error}"));
                None
            }
        },
        Err(error) => {
            errors.push(format!("Windows desktop capture failed: {error}"));
            None
        }
    };
    let start_delta_ms = webview2
        .as_ref()
        .zip(desktop.as_ref())
        .map(|(webview, desktop)| {
            webview
                .capture_started_unix_ms
                .abs_diff(desktop.capture_started_unix_ms)
        });
    let record = RenderEvidenceRecord {
        schema_version: RENDER_EVIDENCE_SCHEMA_VERSION,
        capture_id: capture_id.clone(),
        trigger: trigger.to_string(),
        hidden_duration_ms,
        capture_started_at,
        capture_completed_at: Local::now().to_rfc3339(),
        start_delta_ms,
        target_window,
        graphics,
        windows_shell_snapshot,
        webview2,
        desktop,
        errors,
    };
    publish_record(evidence_root, &capture_id, &record)?;
    Ok(record)
}

fn staging_dir(evidence_root: &Path, capture_id: &str) -> PathBuf {
    evidence_root.join(format!("capture-{capture_id}.staging"))
}

fn write_captured_image(
    evidence_root: &Path,
    capture_id: &str,
    file_name: &str,
    image: CapturedImage,
) -> Result<RenderImageEvidence, String> {
    if image.bytes.len() > MAX_IMAGE_BYTES {
        return Err(format!(
            "{file_name} exceeds the {} byte per-image limit",
            MAX_IMAGE_BYTES
        ));
    }
    let directory = staging_dir(evidence_root, capture_id);
    fs::create_dir_all(&directory)
        .map_err(|error| format!("create render evidence staging directory failed: {error}"))?;
    let path = directory.join(file_name);
    fs::write(&path, &image.bytes)
        .map_err(|error| format!("write render evidence {file_name} failed: {error}"))?;
    Ok(RenderImageEvidence {
        file_name: file_name.to_string(),
        capture_started_unix_ms: image.started_unix_ms,
        capture_completed_unix_ms: image.completed_unix_ms,
        width: image.width,
        height: image.height,
        bytes: image.bytes.len() as u64,
        sha256: format!("{:x}", Sha256::digest(&image.bytes)),
        pixel_metrics: image.pixel_metrics,
    })
}

fn publish_record(
    evidence_root: &Path,
    capture_id: &str,
    record: &RenderEvidenceRecord,
) -> Result<(), String> {
    fs::create_dir_all(evidence_root)
        .map_err(|error| format!("create render evidence root failed: {error}"))?;
    let staging = staging_dir(evidence_root, capture_id);
    fs::create_dir_all(&staging)
        .map_err(|error| format!("create render evidence staging directory failed: {error}"))?;
    let metadata = serde_json::to_vec_pretty(record)
        .map_err(|error| format!("serialize render evidence metadata failed: {error}"))?;
    fs::write(staging.join(RENDER_EVIDENCE_METADATA_FILE), metadata)
        .map_err(|error| format!("write render evidence metadata failed: {error}"))?;

    let latest = evidence_root.join(RENDER_EVIDENCE_LATEST_DIR);
    let previous = evidence_root.join(format!("capture-{capture_id}.previous"));
    if let Ok(metadata) = fs::symlink_metadata(&latest) {
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err("existing render evidence latest path is not a regular directory".into());
        }
        fs::rename(&latest, &previous)
            .map_err(|error| format!("stage previous render evidence failed: {error}"))?;
    }
    if let Err(error) = fs::rename(&staging, &latest) {
        if previous.exists() {
            let _ = fs::rename(&previous, &latest);
        }
        return Err(format!("publish render evidence failed: {error}"));
    }
    if previous.exists() {
        if let Err(error) = fs::remove_dir_all(&previous) {
            log::warn!("remove previous render evidence failed after publish: {error}");
        }
    }
    Ok(())
}

fn target_window_snapshot(window: &WebviewWindow<Wry>) -> Value {
    let outer_position = window.outer_position().ok();
    let outer_size = window.outer_size().ok();
    json!({
        "label": window.label(),
        "hwnd": window.hwnd().ok().map(|hwnd| format!("0x{:x}", hwnd.0 as usize)),
        "visible": window.is_visible().ok(),
        "minimized": window.is_minimized().ok(),
        "maximized": window.is_maximized().ok(),
        "scaleFactor": window.scale_factor().ok(),
        "outerPosition": outer_position.map(|position| json!({"x": position.x, "y": position.y})),
        "outerSize": outer_size.map(|size| json!({"width": size.width, "height": size.height})),
    })
}

async fn capture_webview_preview(window: &WebviewWindow<Wry>) -> Result<CapturedImage, String> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let sender = Arc::new(Mutex::new(Some(sender)));
    let closure_sender = sender.clone();
    window
        .with_webview(move |webview| {
            let started_unix_ms = unix_time_ms();
            let result = (|| -> Result<(), String> {
                let core: ICoreWebView2 = unsafe { webview.controller().CoreWebView2() }
                    .map_err(|error| format!("get CoreWebView2 failed: {error}"))?;
                let stream = unsafe { CreateStreamOnHGlobal(HGLOBAL::default(), true) }
                    .map_err(|error| format!("create CapturePreview stream failed: {error}"))?;
                let callback_stream = stream.clone();
                let callback_sender = closure_sender.clone();
                let handler = CapturePreviewCompletedHandler::create(Box::new(move |result| {
                    let completed = result
                        .map_err(|error| format!("CapturePreview completion failed: {error}"))
                        .and_then(|_| read_preview_stream(&callback_stream, started_unix_ms));
                    send_capture_result(&callback_sender, completed);
                    Ok(())
                }));
                unsafe {
                    core.CapturePreview(
                        COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG,
                        &stream,
                        &handler,
                    )
                }
                .map_err(|error| format!("start CapturePreview failed: {error}"))?;
                Ok(())
            })();
            if let Err(error) = result {
                send_capture_result(&closure_sender, Err(error));
            }
        })
        .map_err(|error| format!("schedule WebView2 CapturePreview failed: {error}"))?;

    tokio::time::timeout(
        std::time::Duration::from_secs(CAPTURE_TIMEOUT_SECONDS),
        receiver,
    )
    .await
    .map_err(|_| format!("CapturePreview timed out after {CAPTURE_TIMEOUT_SECONDS} seconds"))?
    .map_err(|_| "CapturePreview callback was cancelled".to_string())?
}

fn send_capture_result(
    sender: &Arc<Mutex<Option<tokio::sync::oneshot::Sender<Result<CapturedImage, String>>>>>,
    result: Result<CapturedImage, String>,
) {
    if let Ok(mut sender) = sender.lock() {
        if let Some(sender) = sender.take() {
            let _ = sender.send(result);
        }
    }
}

fn read_preview_stream(
    stream: &windows::Win32::System::Com::IStream,
    started_unix_ms: u64,
) -> Result<CapturedImage, String> {
    let mut stat = STATSTG::default();
    unsafe { stream.Stat(&mut stat, STATFLAG_NONAME) }
        .map_err(|error| format!("read CapturePreview stream size failed: {error}"))?;
    if stat.cbSize == 0 || stat.cbSize > MAX_IMAGE_BYTES as u64 {
        return Err(format!(
            "CapturePreview stream size {} is invalid",
            stat.cbSize
        ));
    }
    unsafe { stream.Seek(0, STREAM_SEEK_SET, None) }
        .map_err(|error| format!("rewind CapturePreview stream failed: {error}"))?;
    let mut bytes = vec![0_u8; stat.cbSize as usize];
    let mut read = 0_u32;
    unsafe {
        stream.Read(
            bytes.as_mut_ptr().cast::<c_void>(),
            bytes.len() as u32,
            Some(&mut read),
        )
    }
    .ok()
    .map_err(|error| format!("read CapturePreview stream failed: {error}"))?;
    if read as usize != bytes.len() {
        return Err(format!(
            "CapturePreview stream was truncated: expected {}, got {read}",
            bytes.len()
        ));
    }
    let (width, height, pixel_metrics) = decode_png_metrics(&bytes)?;
    Ok(CapturedImage {
        bytes,
        started_unix_ms,
        completed_unix_ms: unix_time_ms(),
        width,
        height,
        pixel_metrics,
    })
}

fn capture_presented_desktop() -> Result<CapturedImage, String> {
    let x = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
    let y = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
    let width = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
    let height = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };
    if width <= 0 || height <= 0 {
        return Err(format!(
            "invalid virtual desktop bounds {x},{y} {width}x{height}"
        ));
    }
    let byte_len = (width as usize)
        .checked_mul(height as usize)
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| "virtual desktop pixel buffer size overflowed".to_string())?;
    if byte_len > MAX_DECODED_IMAGE_BYTES {
        return Err(format!(
            "virtual desktop pixel buffer is too large: {byte_len} bytes"
        ));
    }
    let started_unix_ms = unix_time_ms();
    let screen_dc = unsafe { GetDC(None) };
    if screen_dc.0.is_null() {
        return Err("GetDC(NULL) failed".into());
    }
    let memory_dc = unsafe { CreateCompatibleDC(Some(screen_dc)) };
    if memory_dc.0.is_null() {
        unsafe {
            ReleaseDC(None, screen_dc);
        }
        return Err("CreateCompatibleDC failed".into());
    }
    let mut bits: *mut c_void = std::ptr::null_mut();
    let bitmap_info = BITMAPINFO {
        bmiHeader: BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width,
            biHeight: -height,
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB.0,
            biSizeImage: byte_len as u32,
            ..Default::default()
        },
        ..Default::default()
    };
    let bitmap = unsafe {
        CreateDIBSection(
            Some(screen_dc),
            &bitmap_info,
            DIB_RGB_COLORS,
            &mut bits,
            None,
            0,
        )
    };
    let bitmap = match bitmap {
        Ok(bitmap) => bitmap,
        Err(error) => {
            unsafe {
                let _ = DeleteDC(memory_dc);
                ReleaseDC(None, screen_dc);
            }
            return Err(format!("CreateDIBSection failed: {error}"));
        }
    };
    let previous = unsafe { SelectObject(memory_dc, HGDIOBJ(bitmap.0)) };
    let select_failed = previous.0.is_null() || previous.0 as isize == -1;
    let capture_result = if select_failed || bits.is_null() {
        Err("SelectObject or DIB pixel allocation failed".to_string())
    } else {
        let raster_operation = ROP_CODE(SRCCOPY.0 | CAPTUREBLT.0);
        unsafe {
            BitBlt(
                memory_dc,
                0,
                0,
                width,
                height,
                Some(screen_dc),
                x,
                y,
                raster_operation,
            )
        }
        .map_err(|error| format!("BitBlt virtual desktop failed: {error}"))
        .map(|_| {
            let bgra = unsafe { std::slice::from_raw_parts(bits.cast::<u8>(), byte_len) };
            let mut rgba = Vec::with_capacity(byte_len);
            for pixel in bgra.chunks_exact(4) {
                rgba.extend_from_slice(&[pixel[2], pixel[1], pixel[0], 255]);
            }
            rgba
        })
    };
    unsafe {
        if !select_failed {
            SelectObject(memory_dc, previous);
        }
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory_dc);
        ReleaseDC(None, screen_dc);
    }
    let rgba = capture_result?;
    let pixel_metrics = metrics_from_rgba(&rgba);
    let bytes = encode_rgba_png(width as u32, height as u32, &rgba)?;
    Ok(CapturedImage {
        bytes,
        started_unix_ms,
        completed_unix_ms: unix_time_ms(),
        width: width as u32,
        height: height as u32,
        pixel_metrics,
    })
}

fn encode_rgba_png(width: u32, height: u32, rgba: &[u8]) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut bytes, width, height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder
            .write_header()
            .map_err(|error| format!("write desktop PNG header failed: {error}"))?;
        writer
            .write_image_data(rgba)
            .map_err(|error| format!("write desktop PNG pixels failed: {error}"))?;
    }
    Ok(bytes)
}

fn decode_png_metrics(bytes: &[u8]) -> Result<(u32, u32, RenderPixelMetrics), String> {
    let mut decoder = png::Decoder::new(Cursor::new(bytes));
    decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut reader = decoder
        .read_info()
        .map_err(|error| format!("decode CapturePreview PNG header failed: {error}"))?;
    let decoded_bytes = (reader.info().width as usize)
        .checked_mul(reader.info().height as usize)
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| "CapturePreview decoded size overflowed".to_string())?;
    if decoded_bytes > MAX_DECODED_IMAGE_BYTES {
        return Err(format!(
            "CapturePreview decoded size {decoded_bytes} exceeds the safety limit"
        ));
    }
    let mut buffer = vec![0_u8; reader.output_buffer_size()];
    let output = reader
        .next_frame(&mut buffer)
        .map_err(|error| format!("decode CapturePreview PNG pixels failed: {error}"))?;
    let pixels = &buffer[..output.buffer_size()];
    let mut rgba = Vec::with_capacity(output.width as usize * output.height as usize * 4);
    match output.color_type {
        png::ColorType::Rgba => rgba.extend_from_slice(pixels),
        png::ColorType::Rgb => {
            for pixel in pixels.chunks_exact(3) {
                rgba.extend_from_slice(&[pixel[0], pixel[1], pixel[2], 255]);
            }
        }
        png::ColorType::GrayscaleAlpha => {
            for pixel in pixels.chunks_exact(2) {
                rgba.extend_from_slice(&[pixel[0], pixel[0], pixel[0], pixel[1]]);
            }
        }
        png::ColorType::Grayscale => {
            for value in pixels {
                rgba.extend_from_slice(&[*value, *value, *value, 255]);
            }
        }
        png::ColorType::Indexed => {
            return Err("CapturePreview PNG remained indexed after expansion".into());
        }
    }
    Ok((output.width, output.height, metrics_from_rgba(&rgba)))
}

fn metrics_from_rgba(rgba: &[u8]) -> RenderPixelMetrics {
    let total_pixels = (rgba.len() / 4) as u64;
    let mut transparent_pixel_count = 0_u64;
    let mut opaque_pixel_count = 0_u64;
    let mut near_black_opaque_pixel_count = 0_u64;
    let mut luma_sum = 0_f64;
    for pixel in rgba.chunks_exact(4) {
        if pixel[3] < 16 {
            transparent_pixel_count += 1;
            continue;
        }
        opaque_pixel_count += 1;
        let luma = 0.2126 * pixel[0] as f64 + 0.7152 * pixel[1] as f64 + 0.0722 * pixel[2] as f64;
        luma_sum += luma;
        if luma <= 8.0 {
            near_black_opaque_pixel_count += 1;
        }
    }
    RenderPixelMetrics {
        total_pixels,
        transparent_pixel_count,
        opaque_pixel_count,
        near_black_opaque_pixel_count,
        near_black_opaque_ratio: (opaque_pixel_count > 0)
            .then_some(near_black_opaque_pixel_count as f64 / opaque_pixel_count as f64),
        average_opaque_luma: (opaque_pixel_count > 0)
            .then_some(luma_sum / opaque_pixel_count as f64),
    }
}

fn unix_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u64::MAX as u128) as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pixel_metrics_ignore_transparent_pixels_and_count_black_opaque_pixels() {
        let metrics = metrics_from_rgba(&[0, 0, 0, 255, 255, 255, 255, 255, 1, 2, 3, 0]);

        assert_eq!(metrics.total_pixels, 3);
        assert_eq!(metrics.transparent_pixel_count, 1);
        assert_eq!(metrics.opaque_pixel_count, 2);
        assert_eq!(metrics.near_black_opaque_pixel_count, 1);
        assert_eq!(metrics.near_black_opaque_ratio, Some(0.5));
    }

    #[test]
    fn encoded_rgba_png_round_trips_metrics() {
        let bytes = encode_rgba_png(2, 1, &[0, 0, 0, 255, 255, 255, 255, 255]).unwrap();
        let (width, height, metrics) = decode_png_metrics(&bytes).unwrap();

        assert_eq!((width, height), (2, 1));
        assert_eq!(metrics.near_black_opaque_ratio, Some(0.5));
    }
}
