use crate::pet_input_evidence::{
    integration::{metadata, record_native},
    PetInputEvidence,
};
use serde_json::{json, Value};
use tauri::{Manager, Runtime, WebviewWindow};

#[cfg(target_os = "windows")]
use std::sync::atomic::{AtomicU64, Ordering};

const PET_WINDOW_LABEL: &str = "pet";

// Windows 上 Tauri/tao 的 set_ignore_cursor_events(false) 会同时移除
// WS_EX_TRANSPARENT 与 WS_EX_LAYERED。pet 是覆盖虚拟桌面的透明置顶窗，移除两者后
// 可能被 Chromium/CEF 误判成整屏不透明遮挡，令下方视频停止绘制。
//
// WS_EX_LAYERED 不能在可交互态强行保留：Windows 会按 layered window 的像素 alpha
// 做 hit-test，而 WebView2 的合成内容不保证进入顶层 HWND 的 alpha bitmap，实测会让
// 桌宠本身也穿透。此适配层因此保留 Tauri 原本的两位联动，只额外永久添加
// WS_EX_TOOLWINDOW；Chromium 明确把 tool window 排除在 native occlusion 计算之外。

#[tauri::command]
pub async fn set_pet_cursor_passthrough(
    window: WebviewWindow,
    ignore: bool,
    diagnostic: Option<Value>,
) -> Result<(), String> {
    if window.label() != PET_WINDOW_LABEL {
        return Err(format!(
            "pet cursor passthrough: window {} is not allowed",
            window.label()
        ));
    }

    let manager = window
        .try_state::<PetInputEvidence>()
        .map(|state| state.inner().clone());
    // Bound optional diagnostics before cloning them into the main-thread closure.
    // Invalid metadata degrades observation only; the business ignore request still runs.
    let diagnostic = match metadata(diagnostic.as_ref()) {
        Ok(fields) if !fields.is_empty() => Some(Value::Object(fields)),
        Ok(_) => None,
        Err(()) => {
            if let Some(manager) = manager.as_ref() {
                manager.note_invalid();
            }
            None
        }
    };
    record_native(
        manager.as_ref(),
        json!({"kind":"passthrough-request","stage":"request","ignore":ignore}),
        diagnostic.as_ref(),
    );
    set_ignore_cursor_events(&window, ignore, manager, diagnostic).await
}

#[cfg(target_os = "windows")]
async fn set_ignore_cursor_events<R: Runtime>(
    window: &WebviewWindow<R>,
    ignore: bool,
    manager: Option<PetInputEvidence>,
    diagnostic: Option<Value>,
) -> Result<(), String> {
    let hwnd = window.hwnd().map_err(|e| {
        record_native(
            manager.as_ref(),
            json!({"kind":"passthrough-applied","ignore":ignore,
            "outcome":"not-applied","stage":"request","errorKind":"window-missing"}),
            diagnostic.as_ref(),
        );
        e.to_string()
    })?;
    let hwnd_value = hwnd.0 as isize;
    let (result_tx, result_rx) = tokio::sync::oneshot::channel();
    let apply_manager = manager.clone();
    let apply_diagnostic = diagnostic.clone();

    window
        .run_on_main_thread(move || {
            let hwnd = windows::Win32::Foundation::HWND(hwnd_value as *mut std::ffi::c_void);
            let _ = result_tx.send(apply_windows_cursor_passthrough(
                hwnd,
                ignore,
                apply_manager.as_ref(),
                apply_diagnostic.as_ref(),
            ));
        })
        .map_err(|e| {
            record_native(
                manager.as_ref(),
                json!({"kind":"passthrough-applied","ignore":ignore,
                "outcome":"not-applied","stage":"request","errorKind":"invoke-failed"}),
                diagnostic.as_ref(),
            );
            e.to_string()
        })?;

    result_rx.await.map_err(|_| {
        // Cancellation says nothing about whether the closure has already applied styles.
        record_native(
            manager.as_ref(),
            json!({"kind":"passthrough-ack","ignore":ignore,
                "outcome":"unknown","stage":"ack","errorKind":"invoke-failed"}),
            diagnostic.as_ref(),
        );
        "pet cursor passthrough: main-thread task was cancelled".to_string()
    })?
}

#[cfg(not(target_os = "windows"))]
async fn set_ignore_cursor_events<R: Runtime>(
    window: &WebviewWindow<R>,
    ignore: bool,
    manager: Option<PetInputEvidence>,
    diagnostic: Option<Value>,
) -> Result<(), String> {
    let result = window
        .set_ignore_cursor_events(ignore)
        .map_err(|e| e.to_string());
    record_native(
        manager.as_ref(),
        json!({"kind":"passthrough-applied","ignore":ignore,
        "outcome":if result.is_ok() {"ok"} else {"error"},"stage":"apply","verified":null}),
        diagnostic.as_ref(),
    );
    result
}

fn windows_cursor_passthrough_style(
    current: isize,
    layered: isize,
    transparent: isize,
    tool_window: isize,
    ignore: bool,
) -> isize {
    let with_tool_window = current | tool_window;
    if ignore {
        with_tool_window | layered | transparent
    } else {
        with_tool_window & !layered & !transparent
    }
}

#[cfg(target_os = "windows")]
fn apply_windows_cursor_passthrough(
    hwnd: windows::Win32::Foundation::HWND,
    ignore: bool,
    manager: Option<&PetInputEvidence>,
    diagnostic: Option<&Value>,
) -> Result<(), String> {
    use windows::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, SetWindowPos, GWL_EXSTYLE, SWP_FRAMECHANGED,
        SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, WS_EX_LAYERED, WS_EX_TOOLWINDOW,
        WS_EX_TRANSPARENT,
    };

    static APPLY_SEQUENCE: AtomicU64 = AtomicU64::new(0);
    // Allocated inside the actual main-thread operation, not at command receipt or ack.
    let apply_seq = APPLY_SEQUENCE.fetch_add(1, Ordering::Relaxed) + 1;
    let layered = WS_EX_LAYERED.0 as isize;
    let transparent = WS_EX_TRANSPARENT.0 as isize;
    let tool_window = WS_EX_TOOLWINDOW.0 as isize;
    let current = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) };
    let expected =
        windows_cursor_passthrough_style(current, layered, transparent, tool_window, ignore);

    if expected != current {
        unsafe {
            SetWindowLongPtrW(hwnd, GWL_EXSTYLE, expected);
            SetWindowPos(
                hwnd,
                None,
                0,
                0,
                0,
                0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
            )
            .map_err(|e| {
                record_native(manager, json!({"kind":"passthrough-applied","ignore":ignore,
                    "applySeq":apply_seq,"styleBefore":current as u32,"styleExpected":expected as u32,
                    "styleActual":null,"verified":null,"outcome":"partial","stage":"refresh",
                    "errorKind":"style-refresh-failed","errorCode":e.code().0}), diagnostic);
                format!("pet cursor passthrough: refresh window style failed: {e}")
            })?;
        }
    }

    let actual = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) };
    let tool_window_preserved = actual & tool_window != 0;
    let layered_matches = (actual & layered != 0) == ignore;
    let transparent_matches = (actual & transparent != 0) == ignore;
    if !tool_window_preserved || !layered_matches || !transparent_matches {
        record_native(
            manager,
            json!({"kind":"passthrough-applied","ignore":ignore,
            "applySeq":apply_seq,"styleBefore":current as u32,"styleExpected":expected as u32,
            "styleActual":actual as u32,"verified":false,"outcome":"error","stage":"verify",
            "errorKind":"style-verification-failed"}),
            diagnostic,
        );
        return Err(format!(
            "pet cursor passthrough: style verification failed (ignore={ignore}, ex_style=0x{actual:x})"
        ));
    }

    record_native(
        manager,
        json!({"kind":"passthrough-applied","ignore":ignore,
        "applySeq":apply_seq,"styleBefore":current as u32,"styleExpected":expected as u32,
        "styleActual":actual as u32,"verified":true,"outcome":if expected == current {"unchanged"} else {"applied"},
        "stage":"verify"}),
        diagnostic,
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const LAYERED: isize = 0x0008_0000;
    const TRANSPARENT: isize = 0x0000_0020;
    const TOOL_WINDOW: isize = 0x0000_0080;
    const UNRELATED: isize = 0x0000_0008;

    #[test]
    fn interactive_style_restores_input_and_marks_window_as_tool_window() {
        let style = windows_cursor_passthrough_style(
            UNRELATED | LAYERED | TRANSPARENT,
            LAYERED,
            TRANSPARENT,
            TOOL_WINDOW,
            false,
        );

        assert_eq!(style & LAYERED, 0);
        assert_eq!(style & TRANSPARENT, 0);
        assert_eq!(style & TOOL_WINDOW, TOOL_WINDOW);
        assert_eq!(style & UNRELATED, UNRELATED);
    }

    #[test]
    fn ignored_style_adds_passthrough_and_tool_window_styles() {
        let style =
            windows_cursor_passthrough_style(UNRELATED, LAYERED, TRANSPARENT, TOOL_WINDOW, true);

        assert_eq!(style & LAYERED, LAYERED);
        assert_eq!(style & TRANSPARENT, TRANSPARENT);
        assert_eq!(style & TOOL_WINDOW, TOOL_WINDOW);
        assert_eq!(style & UNRELATED, UNRELATED);
    }
}
