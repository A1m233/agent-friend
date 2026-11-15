use crate::{ChatOpenRequest, WorkspaceTarget};
use tauri::{
    AppHandle, LogicalSize, Manager, PhysicalSize, State, WebviewUrl, WebviewWindowBuilder, Window,
};
use tokio::sync::Mutex;

const LABEL: &str = "onboarding";
const STANDARD_SIZE: LogicalSize<f64> = LogicalSize::new(820.0, 660.0);

fn fitted_inner_size(
    scale: f64,
    work_area: PhysicalSize<u32>,
    outer: PhysicalSize<u32>,
    inner: PhysicalSize<u32>,
) -> PhysicalSize<u32> {
    let desired = STANDARD_SIZE.to_physical::<u32>(scale);
    let fit = |standard: u32, available: u32, outer: u32, inner: u32| {
        standard
            .min(available.saturating_sub(outer.saturating_sub(inner)))
            .max(1)
    };
    PhysicalSize::new(
        fit(desired.width, work_area.width, outer.width, inner.width),
        fit(desired.height, work_area.height, outer.height, inner.height),
    )
}

pub(crate) fn fit_to_monitor(window: &Window) -> tauri::Result<()> {
    if window.is_minimized()? {
        return Ok(());
    }
    if let Some(monitor) = window.current_monitor()? {
        let inner = window.inner_size()?;
        let target = fitted_inner_size(
            monitor.scale_factor(),
            monitor.work_area().size,
            window.outer_size()?,
            inner,
        );
        if inner != target {
            window.set_size(target)?;
        }
    }
    Ok(())
}

// Serialize creation and keep a step request available until the WebView has subscribed.
#[derive(Default)]
pub(crate) struct WindowState(Mutex<Option<String>>);

pub(crate) async fn show(app: &AppHandle, step: Option<String>) -> Result<(), String> {
    let state = app.state::<WindowState>();
    let mut pending = state.0.lock().await;
    if app.get_webview_window(LABEL).is_none() {
        *pending = None;
        let window =
            WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("onboarding.html".into()))
                .title("agent-friend · 设置助理")
                .inner_size(STANDARD_SIZE.width, STANDARD_SIZE.height)
                .resizable(false)
                .maximizable(false)
                .prevent_overflow()
                .center()
                .visible(false)
                .build()
                .map_err(|e| e.to_string())?;
        fit_to_monitor(&window.as_ref().window()).map_err(|e| e.to_string())?;
    }
    if step.is_some() {
        *pending = step;
    }
    crate::show_and_focus(app, LABEL)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fits_the_decorated_window_to_the_work_area_at_the_monitors_dpi() {
        let size = PhysicalSize::new;
        let fit = |work_area| fitted_inner_size(1.25, work_area, size(1033, 865), size(1025, 825));
        assert_eq!(fit(size(1920, 1040)), size(1025, 825));
        assert_eq!(fit(size(1280, 768)), size(1025, 728));
        assert_eq!(fit(size(900, 760)), size(892, 720));
    }
}

#[tauri::command]
pub(crate) async fn open_onboarding(app: AppHandle) -> Result<(), String> {
    show(&app, None).await
}

#[tauri::command]
pub(crate) async fn take_onboarding_step(
    state: State<'_, WindowState>,
) -> Result<Option<String>, String> {
    Ok(state.0.lock().await.take())
}

#[tauri::command]
pub(crate) async fn finish_onboarding(app: AppHandle) -> Result<(), String> {
    let status = super::get_onboarding_status(app.clone()).await?;
    if !status.chat_ready || !status.memory_ready {
        return Err("基础配置有变化，请重新确认模型。".into());
    }
    crate::navigate_workspace_with_context(
        &app,
        WorkspaceTarget::Chat,
        Some(ChatOpenRequest {
            new_conversation: true,
            session_id: None,
            onboarding_model: Some(status.chat_model),
        }),
        None,
    )?;
    if let Some(window) = app.get_webview_window(LABEL) {
        window.close().map_err(|e| e.to_string())?;
    }
    Ok(())
}
