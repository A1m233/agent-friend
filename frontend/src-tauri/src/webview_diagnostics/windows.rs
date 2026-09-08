use std::sync::Arc;

use tauri::{AppHandle, Manager, WebviewWindow, Wry};
use webview2_com::{
    take_pwstr, BrowserProcessExitedEventHandler,
    Microsoft::Web::WebView2::Win32::{
        ICoreWebView2, ICoreWebView2Environment, ICoreWebView2Environment11,
        ICoreWebView2Environment5, ICoreWebView2ProcessFailedEventArgs,
        ICoreWebView2ProcessFailedEventArgs2, ICoreWebView2_2,
        COREWEBVIEW2_BROWSER_PROCESS_EXIT_KIND, COREWEBVIEW2_BROWSER_PROCESS_EXIT_KIND_FAILED,
        COREWEBVIEW2_BROWSER_PROCESS_EXIT_KIND_NORMAL, COREWEBVIEW2_PROCESS_FAILED_KIND,
        COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_FRAME_RENDER_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_GPU_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_BROKER_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_PLUGIN_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_UNRESPONSIVE,
        COREWEBVIEW2_PROCESS_FAILED_KIND_SANDBOX_HELPER_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_UNKNOWN_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_KIND_UTILITY_PROCESS_EXITED,
        COREWEBVIEW2_PROCESS_FAILED_REASON, COREWEBVIEW2_PROCESS_FAILED_REASON_CRASHED,
        COREWEBVIEW2_PROCESS_FAILED_REASON_LAUNCH_FAILED,
        COREWEBVIEW2_PROCESS_FAILED_REASON_OUT_OF_MEMORY,
        COREWEBVIEW2_PROCESS_FAILED_REASON_PROFILE_DELETED,
        COREWEBVIEW2_PROCESS_FAILED_REASON_TERMINATED,
        COREWEBVIEW2_PROCESS_FAILED_REASON_UNEXPECTED,
        COREWEBVIEW2_PROCESS_FAILED_REASON_UNRESPONSIVE,
    },
    ProcessFailedEventHandler,
};
use windows::core::{Interface, PWSTR};

use super::{WebView2RuntimeInfo, WebViewDiagnosticsManager};

pub(super) fn install_existing_windows(app: &AppHandle, manager: &Arc<WebViewDiagnosticsManager>) {
    let mut windows = app.webview_windows().into_values().collect::<Vec<_>>();
    windows.sort_by_key(|window| (window.label() != "pet", window.label().to_string()));
    for window in windows {
        install_window(app, &window, manager);
    }
}

fn install_window(
    app: &AppHandle,
    window: &WebviewWindow<Wry>,
    manager: &Arc<WebViewDiagnosticsManager>,
) {
    let label = window.label().to_string();
    if !manager.claim_window_registration(&label) {
        return;
    }

    let closure_manager = manager.clone();
    let closure_label = label.clone();
    let closure_app = app.clone();
    let scheduled = window.with_webview(move |webview| {
        let result = (|| -> webview2_com::Result<()> {
            let core = unsafe { webview.controller().CoreWebView2()? };
            install_process_failed_handler(&core, &closure_label, &closure_manager, &closure_app)?;
            closure_manager.mark_window_registered(&closure_label);

            if closure_manager.claim_environment_registration() {
                install_environment_handler(&core, &closure_label, &closure_manager, &closure_app)?;
            }
            Ok(())
        })();
        if let Err(error) = result {
            closure_manager.record_registration_failure(
                Some(&closure_label),
                "with-webview",
                error.to_string(),
            );
        }
    });
    if let Err(error) = scheduled {
        manager.record_registration_failure(Some(&label), "schedule", error.to_string());
    }
}

fn install_process_failed_handler(
    core: &ICoreWebView2,
    window_label: &str,
    manager: &Arc<WebViewDiagnosticsManager>,
    app: &AppHandle,
) -> webview2_com::Result<()> {
    let callback_manager = manager.clone();
    let callback_label = window_label.to_string();
    let callback_app = app.clone();
    let handler = ProcessFailedEventHandler::create(Box::new(move |_sender, args| {
        if let Some(args) = args {
            let (kind, reason, exit_code, description) = read_process_failure(&args);
            let should_begin_recovery = callback_manager.record_process_failed(
                &callback_label,
                kind,
                reason.clone(),
                exit_code,
                description,
            );
            if should_begin_recovery {
                crate::pet_display_recovery::handle_gpu_process_failure(
                    &callback_app,
                    reason.as_deref(),
                    exit_code,
                );
            }
        } else {
            callback_manager.record_registration_failure(
                Some(&callback_label),
                "process-failed-callback",
                "event args missing",
            );
        }
        Ok(())
    }));
    let mut token = 0_i64;
    unsafe { core.add_ProcessFailed(&handler, &mut token)? };
    log::info!(
        "webview2 diagnostics: ProcessFailed registered window={} token={}",
        window_label,
        token
    );
    Ok(())
}

fn install_environment_handler(
    core: &ICoreWebView2,
    registration_window: &str,
    manager: &Arc<WebViewDiagnosticsManager>,
    app: &AppHandle,
) -> webview2_com::Result<()> {
    let core2 = core.cast::<ICoreWebView2_2>()?;
    let environment = unsafe { core2.Environment()? };
    manager.set_runtime(read_runtime_info(&environment, registration_window)?);
    install_render_process_observer(&environment, app);

    let environment5 = environment.cast::<ICoreWebView2Environment5>()?;
    let callback_manager = manager.clone();
    let handler = BrowserProcessExitedEventHandler::create(Box::new(move |_sender, args| {
        if let Some(args) = args {
            let mut kind = COREWEBVIEW2_BROWSER_PROCESS_EXIT_KIND::default();
            let mut process_id = 0_u32;
            let result = unsafe {
                args.BrowserProcessExitKind(&mut kind)
                    .and_then(|_| args.BrowserProcessId(&mut process_id))
            };
            match result {
                Ok(()) => callback_manager
                    .record_browser_process_exited(browser_exit_kind_name(kind), process_id),
                Err(error) => callback_manager.record_registration_failure(
                    None,
                    "browser-process-exited-callback",
                    error.to_string(),
                ),
            }
        } else {
            callback_manager.record_registration_failure(
                None,
                "browser-process-exited-callback",
                "event args missing",
            );
        }
        Ok(())
    }));
    let mut token = 0_i64;
    unsafe { environment5.add_BrowserProcessExited(&handler, &mut token)? };
    log::info!(
        "webview2 diagnostics: BrowserProcessExited registered via window={} token={}",
        registration_window,
        token
    );
    Ok(())
}

fn read_runtime_info(
    environment: &ICoreWebView2Environment,
    registration_window: &str,
) -> webview2_com::Result<WebView2RuntimeInfo> {
    let mut version = PWSTR::null();
    unsafe { environment.BrowserVersionString(&mut version)? };
    let failure_report_folder_path = environment
        .cast::<ICoreWebView2Environment11>()
        .ok()
        .and_then(|environment11| {
            let mut value = PWSTR::null();
            unsafe { environment11.FailureReportFolderPath(&mut value) }
                .ok()
                .map(|()| take_pwstr(value))
        });
    Ok(WebView2RuntimeInfo {
        browser_version: take_pwstr(version),
        failure_report_folder_path,
        environment_registration_window: registration_window.to_string(),
    })
}

fn read_process_failure(
    args: &ICoreWebView2ProcessFailedEventArgs,
) -> (String, Option<String>, Option<i32>, Option<String>) {
    let mut kind = COREWEBVIEW2_PROCESS_FAILED_KIND::default();
    let kind_name = unsafe { args.ProcessFailedKind(&mut kind) }
        .map(|()| process_failed_kind_name(kind))
        .unwrap_or_else(|error| format!("unavailable({error})"));

    let Ok(args2) = args.cast::<ICoreWebView2ProcessFailedEventArgs2>() else {
        return (kind_name, None, None, None);
    };
    let mut reason = COREWEBVIEW2_PROCESS_FAILED_REASON::default();
    let reason = unsafe { args2.Reason(&mut reason) }
        .ok()
        .map(|()| process_failed_reason_name(reason));
    let mut exit_code = 0_i32;
    let exit_code = unsafe { args2.ExitCode(&mut exit_code) }
        .ok()
        .map(|()| exit_code);
    let mut description = PWSTR::null();
    let description = unsafe { args2.ProcessDescription(&mut description) }
        .ok()
        .map(|()| take_pwstr(description))
        .filter(|value| !value.is_empty());
    (kind_name, reason, exit_code, description)
}

fn process_failed_kind_name(kind: COREWEBVIEW2_PROCESS_FAILED_KIND) -> String {
    let name = if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED {
        "browser-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED {
        "render-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_UNRESPONSIVE {
        "render-process-unresponsive"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_FRAME_RENDER_PROCESS_EXITED {
        "frame-render-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_UTILITY_PROCESS_EXITED {
        "utility-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_SANDBOX_HELPER_PROCESS_EXITED {
        "sandbox-helper-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_GPU_PROCESS_EXITED {
        "gpu-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_PLUGIN_PROCESS_EXITED {
        "ppapi-plugin-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_BROKER_PROCESS_EXITED {
        "ppapi-broker-process-exited"
    } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_UNKNOWN_PROCESS_EXITED {
        "unknown-process-exited"
    } else {
        return format!("unknown({})", kind.0);
    };
    name.to_string()
}

fn process_failed_reason_name(reason: COREWEBVIEW2_PROCESS_FAILED_REASON) -> String {
    let name = if reason == COREWEBVIEW2_PROCESS_FAILED_REASON_UNEXPECTED {
        "unexpected"
    } else if reason == COREWEBVIEW2_PROCESS_FAILED_REASON_UNRESPONSIVE {
        "unresponsive"
    } else if reason == COREWEBVIEW2_PROCESS_FAILED_REASON_TERMINATED {
        "terminated"
    } else if reason == COREWEBVIEW2_PROCESS_FAILED_REASON_CRASHED {
        "crashed"
    } else if reason == COREWEBVIEW2_PROCESS_FAILED_REASON_LAUNCH_FAILED {
        "launch-failed"
    } else if reason == COREWEBVIEW2_PROCESS_FAILED_REASON_OUT_OF_MEMORY {
        "out-of-memory"
    } else if reason == COREWEBVIEW2_PROCESS_FAILED_REASON_PROFILE_DELETED {
        "profile-deleted"
    } else {
        return format!("unknown({})", reason.0);
    };
    name.to_string()
}

fn browser_exit_kind_name(kind: COREWEBVIEW2_BROWSER_PROCESS_EXIT_KIND) -> String {
    if kind == COREWEBVIEW2_BROWSER_PROCESS_EXIT_KIND_NORMAL {
        "normal".to_string()
    } else if kind == COREWEBVIEW2_BROWSER_PROCESS_EXIT_KIND_FAILED {
        "failed".to_string()
    } else {
        format!("unknown({})", kind.0)
    }
}

fn install_render_process_observer(environment: &ICoreWebView2Environment, app: &AppHandle) {
    use webview2_com::{
        Microsoft::Web::WebView2::Win32::ICoreWebView2Environment8, ProcessInfosChangedEventHandler,
    };
    record_render_processes(environment, app);
    if let Ok(environment8) = environment.cast::<ICoreWebView2Environment8>() {
        let app = app.clone();
        let handler = ProcessInfosChangedEventHandler::create(Box::new(move |sender, _| {
            if let Some(environment) = sender {
                record_render_processes(&environment, &app);
            }
            Ok(())
        }));
        let mut token = 0;
        if unsafe { environment8.add_ProcessInfosChanged(&handler, &mut token) }.is_err() {
            log::warn!("pet render process observer registration failed");
        }
    }
}
fn record_render_processes(environment: &ICoreWebView2Environment, app: &AppHandle) {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        ICoreWebView2Environment8, COREWEBVIEW2_PROCESS_KIND,
    };
    let read = || -> webview2_com::Result<Vec<serde_json::Value>> {
        let env = environment.cast::<ICoreWebView2Environment8>()?;
        let infos = unsafe { env.GetProcessInfos()? };
        let mut count = 0;
        unsafe {
            infos.Count(&mut count)?;
        }
        let mut rows = Vec::new();
        for index in 0..count.min(64) {
            let info = unsafe { infos.GetValueAtIndex(index)? };
            let mut pid = 0;
            let mut kind = COREWEBVIEW2_PROCESS_KIND::default();
            unsafe {
                info.ProcessId(&mut pid)?;
                info.Kind(&mut kind)?;
            }
            if pid > 0 {
                rows.push(serde_json::json!({"pid":pid,"kind":kind.0}));
            }
        }
        Ok(rows)
    };
    let result = read();
    crate::pet_render_evidence::record(
        app,
        serde_json::json!({"kind":"webview-processes",
        "scope":"environment","available":result.is_ok(),"processes":result.unwrap_or_default()}),
    );
}
