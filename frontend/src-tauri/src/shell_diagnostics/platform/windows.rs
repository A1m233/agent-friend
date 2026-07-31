use super::super::model::{
    format_hwnd, now_rfc3339, RawShellEvent, RectSnapshot, ShellSnapshot, WindowSnapshot,
    SHELL_DIAGNOSTICS_SCHEMA_VERSION,
};
use std::{
    collections::BTreeMap,
    ffi::c_void,
    path::Path,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, SyncSender},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::Duration,
};
use windows::{
    core::{w, Error as WindowsError, BOOL, PWSTR},
    Win32::{
        Foundation::{CloseHandle, HWND, LPARAM, RECT, WPARAM},
        Graphics::{
            Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS},
            Gdi::{GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST},
        },
        System::Threading::{
            GetCurrentThreadId, OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
            PROCESS_QUERY_LIMITED_INFORMATION,
        },
        UI::{
            Accessibility::{SetWinEventHook, UnhookWinEvent, HWINEVENTHOOK},
            Shell::{SHAppBarMessage, ABM_GETSTATE, ABS_ALWAYSONTOP, ABS_AUTOHIDE, APPBARDATA},
            WindowsAndMessaging::{
                DispatchMessageW, EnumWindows, GetClassNameW, GetForegroundWindow, GetMessageW,
                GetPropW, GetWindowLongPtrW, GetWindowRect, GetWindowThreadProcessId, IsIconic,
                IsWindowVisible, IsZoomed, PeekMessageW, PostThreadMessageW, EVENT_OBJECT_CLOAKED,
                EVENT_OBJECT_CREATE, EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE,
                EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_REORDER, EVENT_OBJECT_SHOW,
                EVENT_OBJECT_UNCLOAKED, EVENT_SYSTEM_DESKTOPSWITCH, EVENT_SYSTEM_FOREGROUND,
                EVENT_SYSTEM_MINIMIZEEND, EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MOVESIZEEND,
                EVENT_SYSTEM_MOVESIZESTART, GWL_EXSTYLE, GWL_STYLE, MSG, OBJID_WINDOW, PM_NOREMOVE,
                WINEVENT_OUTOFCONTEXT, WM_QUIT, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
                WS_EX_TOPMOST, WS_EX_TRANSPARENT,
            },
        },
    },
};

const FULLSCREEN_RECT_TOLERANCE: i32 = 2;
const EVENT_QUEUE_START_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_FULLSCREEN_CANDIDATES: usize = 64;

#[derive(Clone)]
struct EventSink {
    sender: SyncSender<RawShellEvent>,
    dropped_events: Arc<AtomicU64>,
}

static EVENT_SINK: Mutex<Option<EventSink>> = Mutex::new(None);

pub(crate) struct ObserverHandle {
    thread_id: u32,
    join: Option<JoinHandle<()>>,
}

impl ObserverHandle {
    pub(crate) fn stop(mut self) {
        if let Err(error) =
            unsafe { PostThreadMessageW(self.thread_id, WM_QUIT, WPARAM(0), LPARAM(0)) }
        {
            log::warn!("windows shell diagnostics observer stop signal failed: {error}");
        }
        if let Some(join) = self.join.take() {
            if join.join().is_err() {
                log::warn!("windows shell diagnostics observer thread panicked");
            }
        }
        if let Ok(mut sink) = EVENT_SINK.lock() {
            *sink = None;
        }
    }
}

pub(crate) fn start_observer(
    event_tx: SyncSender<RawShellEvent>,
    dropped_events: Arc<AtomicU64>,
) -> Result<ObserverHandle, String> {
    {
        let mut sink = EVENT_SINK
            .lock()
            .map_err(|_| "windows shell diagnostics event sink is poisoned".to_string())?;
        if sink.is_some() {
            return Err("windows shell diagnostics observer is already running".into());
        }
        *sink = Some(EventSink {
            sender: event_tx,
            dropped_events,
        });
    }

    let (ready_tx, ready_rx) = mpsc::sync_channel(1);
    let cancelled = Arc::new(AtomicBool::new(false));
    let observer_cancelled = cancelled.clone();
    let join = match thread::Builder::new()
        .name("windows-shell-events".into())
        .spawn(move || observer_thread(ready_tx, observer_cancelled))
    {
        Ok(join) => join,
        Err(error) => {
            if let Ok(mut sink) = EVENT_SINK.lock() {
                *sink = None;
            }
            return Err(format!(
                "start windows shell diagnostics observer failed: {error}"
            ));
        }
    };

    match ready_rx.recv_timeout(EVENT_QUEUE_START_TIMEOUT) {
        Ok(Ok(thread_id)) => Ok(ObserverHandle {
            thread_id,
            join: Some(join),
        }),
        Ok(Err(error)) => {
            let _ = join.join();
            if let Ok(mut sink) = EVENT_SINK.lock() {
                *sink = None;
            }
            Err(error)
        }
        Err(error) => {
            cancelled.store(true, Ordering::Release);
            if let Ok(mut sink) = EVENT_SINK.lock() {
                *sink = None;
            }
            Err(format!(
                "windows shell diagnostics observer startup timed out: {error}"
            ))
        }
    }
}

fn observer_thread(ready_tx: mpsc::SyncSender<Result<u32, String>>, cancelled: Arc<AtomicBool>) {
    let mut message = MSG::default();
    unsafe {
        let _ = PeekMessageW(&mut message, None, 0, 0, PM_NOREMOVE);
    }
    let hooks = match install_hooks() {
        Ok(hooks) => hooks,
        Err(error) => {
            let _ = ready_tx.send(Err(error));
            return;
        }
    };

    if cancelled.load(Ordering::Acquire) {
        uninstall_hooks(hooks);
        return;
    }
    let thread_id = unsafe { GetCurrentThreadId() };
    if ready_tx.send(Ok(thread_id)).is_err() {
        uninstall_hooks(hooks);
        return;
    }

    loop {
        let result = unsafe { GetMessageW(&mut message, None, 0, 0) };
        if result.0 == -1 {
            log::warn!(
                "windows shell diagnostics observer message loop failed: {}",
                WindowsError::from_win32()
            );
            break;
        }
        if result.0 == 0 {
            break;
        }
        unsafe {
            DispatchMessageW(&message);
        }
    }

    uninstall_hooks(hooks);
}

fn uninstall_hooks(hooks: Vec<HWINEVENTHOOK>) {
    for hook in hooks {
        unsafe {
            let _ = UnhookWinEvent(hook);
        }
    }
}

fn install_hooks() -> Result<Vec<HWINEVENTHOOK>, String> {
    let ranges = [
        (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
        (EVENT_SYSTEM_MOVESIZESTART, EVENT_SYSTEM_MOVESIZEEND),
        (EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND),
        (EVENT_SYSTEM_DESKTOPSWITCH, EVENT_SYSTEM_DESKTOPSWITCH),
        (EVENT_OBJECT_CREATE, EVENT_OBJECT_HIDE),
        (EVENT_OBJECT_REORDER, EVENT_OBJECT_REORDER),
        (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
        (EVENT_OBJECT_CLOAKED, EVENT_OBJECT_UNCLOAKED),
    ];
    let mut hooks = Vec::with_capacity(ranges.len());
    for (event_min, event_max) in ranges {
        let hook = unsafe {
            SetWinEventHook(
                event_min,
                event_max,
                None,
                Some(win_event_callback),
                0,
                0,
                WINEVENT_OUTOFCONTEXT,
            )
        };
        if hook.0.is_null() {
            for installed in hooks {
                unsafe {
                    let _ = UnhookWinEvent(installed);
                }
            }
            return Err(format!(
                "register Windows event hook 0x{event_min:x}-0x{event_max:x} failed: {}",
                WindowsError::from_win32()
            ));
        }
        hooks.push(hook);
    }
    Ok(hooks)
}

unsafe extern "system" fn win_event_callback(
    _hook: HWINEVENTHOOK,
    event: u32,
    hwnd: HWND,
    object_id: i32,
    child_id: i32,
    event_thread_id: u32,
    event_uptime_ms: u32,
) {
    if hwnd.0.is_null() {
        return;
    }
    if event >= EVENT_OBJECT_CREATE && object_id != OBJID_WINDOW.0 {
        return;
    }

    let sink = EVENT_SINK
        .try_lock()
        .ok()
        .and_then(|guard| guard.as_ref().cloned());
    let Some(sink) = sink else {
        return;
    };
    let raw_event = RawShellEvent {
        received_at: now_rfc3339(),
        source: "win-event".into(),
        kind: event_name(event).into(),
        event_id: Some(event),
        hwnd: Some(format_hwnd(hwnd.0 as usize)),
        app_window_label: None,
        object_id: Some(object_id),
        child_id: Some(child_id),
        event_thread_id: Some(event_thread_id),
        event_uptime_ms: Some(event_uptime_ms),
    };
    if sink.sender.try_send(raw_event).is_err() {
        sink.dropped_events.fetch_add(1, Ordering::Relaxed);
    }
}

fn event_name(event: u32) -> &'static str {
    match event {
        EVENT_SYSTEM_FOREGROUND => "foreground",
        EVENT_SYSTEM_MOVESIZESTART => "move-size-start",
        EVENT_SYSTEM_MOVESIZEEND => "move-size-end",
        EVENT_SYSTEM_MINIMIZESTART => "minimize-start",
        EVENT_SYSTEM_MINIMIZEEND => "minimize-end",
        EVENT_SYSTEM_DESKTOPSWITCH => "desktop-switch",
        EVENT_OBJECT_CREATE => "window-create",
        EVENT_OBJECT_DESTROY => "window-destroy",
        EVENT_OBJECT_SHOW => "window-show",
        EVENT_OBJECT_HIDE => "window-hide",
        EVENT_OBJECT_REORDER => "window-reorder",
        EVENT_OBJECT_LOCATIONCHANGE => "window-location-change",
        EVENT_OBJECT_CLOAKED => "window-cloaked",
        EVENT_OBJECT_UNCLOAKED => "window-uncloaked",
        _ => "unknown",
    }
}

pub(crate) fn capture_snapshot(app_windows: &BTreeMap<usize, String>) -> ShellSnapshot {
    let captured_at = now_rfc3339();
    let foreground = unsafe { GetForegroundWindow() };
    let foreground_id = (!foreground.0.is_null()).then_some(foreground.0 as usize);
    let mut probe_errors = Vec::new();
    let enumerated = match enumerate_windows() {
        Ok(windows) => windows,
        Err(error) => {
            probe_errors.push(error);
            Vec::new()
        }
    };
    let ranks = enumerated
        .iter()
        .enumerate()
        .map(|(rank, hwnd)| (hwnd.0 as usize, rank as u32))
        .collect::<BTreeMap<_, _>>();
    let mut selected = BTreeMap::<usize, (HWND, Vec<String>)>::new();

    for (hwnd, label) in app_windows {
        add_role(
            &mut selected,
            HWND(*hwnd as *mut c_void),
            format!("agent-friend:{label}"),
        );
    }
    if let Some(hwnd) = foreground_id {
        add_role(&mut selected, foreground, "foreground".into());
        debug_assert_eq!(hwnd, foreground.0 as usize);
    }

    let mut fullscreen_candidate_count = 0usize;
    let mut fullscreen_candidates_truncated = false;
    for hwnd in &enumerated {
        let class_name = window_class(*hwnd);
        if class_name.as_deref() == Some("Shell_TrayWnd") {
            add_role(&mut selected, *hwnd, "taskbar-primary".into());
        } else if class_name.as_deref() == Some("Shell_SecondaryTrayWnd") {
            add_role(&mut selected, *hwnd, "taskbar-secondary".into());
        }
        if is_fullscreen_geometry_candidate(*hwnd) {
            if fullscreen_candidate_count < MAX_FULLSCREEN_CANDIDATES {
                add_role(&mut selected, *hwnd, "fullscreen-geometry-candidate".into());
                fullscreen_candidate_count += 1;
            } else {
                fullscreen_candidates_truncated = true;
            }
        }
    }
    if fullscreen_candidates_truncated {
        probe_errors.push(format!(
            "fullscreen geometry candidates were capped at {MAX_FULLSCREEN_CANDIDATES}"
        ));
    }

    let mut windows = selected
        .into_values()
        .map(|(hwnd, mut roles)| {
            roles.sort();
            roles.dedup();
            snapshot_window(hwnd, roles, ranks.get(&(hwnd.0 as usize)).copied())
        })
        .collect::<Vec<_>>();
    windows.sort_by_key(|window| (window.z_order_rank.unwrap_or(u32::MAX), window.hwnd.clone()));

    let taskbar_state = taskbar_state();
    let taskbar_above_foreground_fullscreen = foreground_id.and_then(|foreground_hwnd| {
        let foreground_window = windows
            .iter()
            .find(|window| window.hwnd == format_hwnd(foreground_hwnd))?;
        if !foreground_window.fullscreen_geometry_candidate {
            return None;
        }
        let foreground_rank = foreground_window.z_order_rank?;
        let taskbar_ranks = windows
            .iter()
            .filter(|window| {
                window.visible && window.roles.iter().any(|role| role.starts_with("taskbar-"))
            })
            .filter_map(|window| window.z_order_rank)
            .collect::<Vec<_>>();
        (!taskbar_ranks.is_empty())
            .then(|| taskbar_ranks.into_iter().any(|rank| rank < foreground_rank))
    });

    ShellSnapshot {
        schema_version: SHELL_DIAGNOSTICS_SCHEMA_VERSION,
        captured_at,
        capability: "supported",
        foreground_hwnd: foreground_id.map(format_hwnd),
        windows,
        taskbar_auto_hide: taskbar_state.map(|state| state & ABS_AUTOHIDE != 0),
        taskbar_always_on_top: taskbar_state.map(|state| state & ABS_ALWAYSONTOP != 0),
        taskbar_above_foreground_fullscreen,
        probe_errors,
    }
}

fn add_role(selected: &mut BTreeMap<usize, (HWND, Vec<String>)>, hwnd: HWND, role: String) {
    if hwnd.0.is_null() {
        return;
    }
    selected
        .entry(hwnd.0 as usize)
        .or_insert_with(|| (hwnd, Vec::new()))
        .1
        .push(role);
}

fn enumerate_windows() -> Result<Vec<HWND>, String> {
    unsafe extern "system" fn callback(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let output = &mut *(lparam.0 as *mut Vec<HWND>);
        output.push(hwnd);
        true.into()
    }

    let mut windows = Vec::new();
    unsafe {
        EnumWindows(
            Some(callback),
            LPARAM((&mut windows as *mut Vec<HWND>) as isize),
        )
        .map_err(|error| format!("enumerate top-level windows failed: {error}"))?;
    }
    Ok(windows)
}

fn snapshot_window(hwnd: HWND, roles: Vec<String>, z_order_rank: Option<u32>) -> WindowSnapshot {
    let mut process_id = 0;
    unsafe {
        GetWindowThreadProcessId(hwnd, Some(&mut process_id));
    }
    let visible = unsafe { IsWindowVisible(hwnd).as_bool() };
    let minimized = unsafe { IsIconic(hwnd).as_bool() };
    let maximized = unsafe { IsZoomed(hwnd).as_bool() };
    let window_rect = window_rect(hwnd);
    let extended_frame_rect = extended_frame_rect(hwnd);
    let (monitor_rect, monitor_work_rect) = monitor_rects(hwnd).unwrap_or((None, None));
    let style = unsafe { GetWindowLongPtrW(hwnd, GWL_STYLE) };
    let extended_style = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) };
    let cloaked = is_cloaked(hwnd);
    let fullscreen_geometry_candidate = visible
        && !minimized
        && cloaked != Some(true)
        && window_rect
            .zip(monitor_rect)
            .is_some_and(|(window, monitor)| window.covers(monitor, FULLSCREEN_RECT_TOLERANCE));

    WindowSnapshot {
        hwnd: format_hwnd(hwnd.0 as usize),
        roles,
        process_id,
        process_name: process_name(process_id),
        class_name: window_class(hwnd),
        visible,
        minimized,
        maximized,
        cloaked,
        window_rect,
        extended_frame_rect,
        monitor_rect,
        monitor_work_rect,
        style: format!("0x{:x}", style as usize),
        extended_style: format!("0x{:x}", extended_style as usize),
        topmost: extended_style & WS_EX_TOPMOST.0 as isize != 0,
        layered: extended_style & WS_EX_LAYERED.0 as isize != 0,
        transparent: extended_style & WS_EX_TRANSPARENT.0 as isize != 0,
        no_activate: extended_style & WS_EX_NOACTIVATE.0 as isize != 0,
        tool_window: extended_style & WS_EX_TOOLWINDOW.0 as isize != 0,
        non_rude_property_present: !unsafe { GetPropW(hwnd, w!("NonRudeHWND")) }.0.is_null(),
        z_order_rank,
        fullscreen_geometry_candidate,
    }
}

fn is_fullscreen_geometry_candidate(hwnd: HWND) -> bool {
    if !unsafe { IsWindowVisible(hwnd).as_bool() } || unsafe { IsIconic(hwnd).as_bool() } {
        return false;
    }
    if is_cloaked(hwnd) == Some(true) {
        return false;
    }
    let Some(window_rect) = window_rect(hwnd) else {
        return false;
    };
    let Some((Some(monitor_rect), _)) = monitor_rects(hwnd) else {
        return false;
    };
    window_rect.covers(monitor_rect, FULLSCREEN_RECT_TOLERANCE)
}

fn window_rect(hwnd: HWND) -> Option<RectSnapshot> {
    let mut rect = RECT::default();
    unsafe { GetWindowRect(hwnd, &mut rect) }
        .ok()
        .map(|_| rect.into())
}

fn extended_frame_rect(hwnd: HWND) -> Option<RectSnapshot> {
    let mut rect = RECT::default();
    unsafe {
        DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            (&mut rect as *mut RECT).cast(),
            std::mem::size_of::<RECT>() as u32,
        )
    }
    .ok()
    .map(|_| rect.into())
}

fn is_cloaked(hwnd: HWND) -> Option<bool> {
    let mut cloaked = 0u32;
    unsafe {
        DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            (&mut cloaked as *mut u32).cast(),
            std::mem::size_of::<u32>() as u32,
        )
    }
    .ok()
    .map(|_| cloaked != 0)
}

fn monitor_rects(hwnd: HWND) -> Option<(Option<RectSnapshot>, Option<RectSnapshot>)> {
    let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
    if monitor.0.is_null() {
        return None;
    }
    let mut info = MONITORINFO {
        cbSize: std::mem::size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    unsafe { GetMonitorInfoW(monitor, &mut info).as_bool() }
        .then(|| (Some(info.rcMonitor.into()), Some(info.rcWork.into())))
}

fn window_class(hwnd: HWND) -> Option<String> {
    let mut buffer = [0u16; 256];
    let length = unsafe { GetClassNameW(hwnd, &mut buffer) };
    (length > 0).then(|| String::from_utf16_lossy(&buffer[..length as usize]))
}

fn process_name(process_id: u32) -> Option<String> {
    if process_id == 0 {
        return None;
    }
    let process =
        unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, process_id) }.ok()?;
    let mut buffer = vec![0u16; 32_768];
    let mut length = buffer.len() as u32;
    let result = unsafe {
        QueryFullProcessImageNameW(
            process,
            PROCESS_NAME_WIN32,
            PWSTR(buffer.as_mut_ptr()),
            &mut length,
        )
    };
    let _ = unsafe { CloseHandle(process) };
    result.ok()?;
    let full_path = String::from_utf16_lossy(&buffer[..length as usize]);
    Path::new(&full_path)
        .file_name()
        .and_then(|name| name.to_str())
        .map(ToOwned::to_owned)
}

fn taskbar_state() -> Option<u32> {
    let mut data = APPBARDATA {
        cbSize: std::mem::size_of::<APPBARDATA>() as u32,
        ..Default::default()
    };
    Some(unsafe { SHAppBarMessage(ABM_GETSTATE, &mut data) } as u32)
}

impl From<RECT> for RectSnapshot {
    fn from(value: RECT) -> Self {
        Self {
            left: value.left,
            top: value.top,
            right: value.right,
            bottom: value.bottom,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn passive_shell_snapshot_probe_is_callable() {
        let snapshot = capture_snapshot(&BTreeMap::new());
        assert_eq!(snapshot.capability, "supported");
        assert_eq!(snapshot.schema_version, SHELL_DIAGNOSTICS_SCHEMA_VERSION);
    }
}
