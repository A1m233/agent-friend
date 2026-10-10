use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{
    plugin::TauriPlugin, AppHandle, Manager, PhysicalPosition, PhysicalSize, Position, Runtime,
    Size, Window,
};
use tauri_plugin_store::StoreExt;
use tauri_plugin_window_state::{AppHandleExt, StateFlags};
use tokio::sync::watch;

use crate::paths;

const WINDOW_PLACEMENTS_KEY: &str = "windowPlacements";
const MANAGED_WINDOW_LABELS: &[&str] = &["chat", "voice-call", "live2d-debugger"];
const DEFAULT_WINDOW_WIDTH: u32 = 720;
const DEFAULT_WINDOW_HEIGHT: u32 = 640;
const SAVE_DELAY: Duration = Duration::from_millis(500);

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct WindowBounds {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct MonitorRect {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

type WindowPlacementMap = BTreeMap<String, WindowBounds>;

struct WindowPlacementState {
    initial: Mutex<BTreeMap<String, InitialPlacement>>,
    center_on_first_show: Mutex<BTreeSet<String>>,
    save_requests: watch::Sender<()>,
    autosave: tauri::async_runtime::JoinHandle<()>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct InitialPlacement {
    bounds: WindowBounds,
    legacy_outer_size: bool,
}

fn state_flags() -> StateFlags {
    StateFlags::SIZE | StateFlags::POSITION | StateFlags::MAXIMIZED
}

pub fn persistence_plugin<R: Runtime>() -> TauriPlugin<R> {
    tauri_plugin_window_state::Builder::default()
        .with_state_flags(state_flags())
        .with_filter(is_managed_window_label)
        .build()
}

/// Product placement policy and legacy migration; the official plugin owns live geometry.
/// Register after the store and window-state plugins, so their state is ready first.
pub fn policy_plugin<R: Runtime>() -> TauriPlugin<R> {
    tauri::plugin::Builder::new("window-placement")
        .setup(|app, _| {
            let initial = load_initial_placements(app).unwrap_or_else(|error| {
                log::warn!("window placement: load initial placement failed: {error}");
                BTreeMap::new()
            });
            let (save_requests, mut receiver) = watch::channel(());
            let handle = app.clone();
            let autosave = tauri::async_runtime::spawn(async move {
                while wait_for_idle(&mut receiver).await {
                    let main_handle = handle.clone();
                    // Never acquire the plugin's cache lock on a worker while its window
                    // getters wait for the event loop (plugins-workspace#3594).
                    if let Err(error) = handle.run_on_main_thread(move || {
                        save_on_main_thread(&main_handle);
                    }) {
                        log::warn!("window placement: schedule save failed: {error}");
                        break;
                    }
                }
            });
            app.manage(WindowPlacementState {
                initial: Mutex::new(initial),
                center_on_first_show: Mutex::new(BTreeSet::new()),
                save_requests,
                autosave,
            });
            Ok(())
        })
        .on_window_ready(|window| {
            if !is_managed_window_label(window.label()) {
                return;
            }
            let state = window.state::<WindowPlacementState>();
            let initial = state.initial.lock().unwrap().remove(window.label());
            if initial.is_none() {
                state
                    .center_on_first_show
                    .lock()
                    .unwrap()
                    .insert(window.label().to_string());
            }
            if let Err(error) = place_initial_window(&window, initial) {
                log::warn!(
                    "window placement: initialize {} failed: {error}",
                    window.label()
                );
            }
            request_save(window.app_handle());
        })
        .on_event(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                app.state::<WindowPlacementState>().autosave.abort();
                // The official plugin saves synchronously on Exit.
            }
        })
        .build()
}

async fn wait_for_idle(receiver: &mut watch::Receiver<()>) -> bool {
    if receiver.changed().await.is_err() {
        return false;
    }
    loop {
        match tokio::time::timeout(SAVE_DELAY, receiver.changed()).await {
            Ok(Ok(())) => continue,
            Ok(Err(_)) => return false,
            Err(_) => return true,
        }
    }
}

pub fn request_save<R: Runtime>(app: &AppHandle<R>) {
    if let Some(state) = app.try_state::<WindowPlacementState>() {
        state.save_requests.send_replace(());
    }
}

/// Called only from native window callbacks or a run_on_main_thread closure.
pub fn save_on_main_thread<R: Runtime>(app: &AppHandle<R>) {
    if let Err(error) = app.save_window_state(state_flags()) {
        log::warn!("window placement: save failed: {error}");
    }
}

pub fn is_managed_window_label(label: &str) -> bool {
    MANAGED_WINDOW_LABELS.contains(&label)
}

fn parse_window_placements(value: Option<serde_json::Value>) -> Result<WindowPlacementMap, String> {
    let Some(value) = value else {
        return Ok(WindowPlacementMap::new());
    };
    serde_json::from_value(value)
        .map_err(|e| format!("ui-state: parse windowPlacements failed: {e}"))
}

fn load_window_placements<R: Runtime>(app: &AppHandle<R>) -> Result<WindowPlacementMap, String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    parse_window_placements(store.get(WINDOW_PLACEMENTS_KEY))
}

fn initial_placements(
    legacy: WindowPlacementMap,
    current: WindowPlacementMap,
) -> BTreeMap<String, InitialPlacement> {
    let mut initial = BTreeMap::new();
    for (placements, legacy_outer_size) in [(legacy, true), (current, false)] {
        for (label, bounds) in placements {
            if is_managed_window_label(&label) && bounds.width > 0 && bounds.height > 0 {
                initial.insert(
                    label,
                    InitialPlacement {
                        bounds,
                        legacy_outer_size,
                    },
                );
            }
        }
    }
    initial
}

fn load_initial_placements<R: Runtime>(
    app: &AppHandle<R>,
) -> Result<BTreeMap<String, InitialPlacement>, String> {
    let path = paths::window_state_path(app).map_err(|error| error.to_string())?;
    // Read only geometry for placement policy/migration. Do not write the plugin's
    // private file format or maintain a second live geometry cache.
    let current = match std::fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|error| error.to_string())?,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
        Err(error) => return Err(error.to_string()),
    };
    Ok(initial_placements(load_window_placements(app)?, current))
}

fn monitor_from_tauri(monitor: &tauri::Monitor) -> MonitorRect {
    let position = monitor.position();
    let size = monitor.size();
    MonitorRect {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    }
}

fn is_usable_monitor(monitor: MonitorRect) -> bool {
    monitor.width > 0 && monitor.height > 0
}

fn rect_right(x: i32, width: u32) -> i32 {
    x.saturating_add(width.min(i32::MAX as u32) as i32)
}

fn rect_bottom(y: i32, height: u32) -> i32 {
    y.saturating_add(height.min(i32::MAX as u32) as i32)
}

fn contains_point(monitor: MonitorRect, x: f64, y: f64) -> bool {
    x >= f64::from(monitor.x)
        && x <= f64::from(rect_right(monitor.x, monitor.width))
        && y >= f64::from(monitor.y)
        && y <= f64::from(rect_bottom(monitor.y, monitor.height))
}

fn distance_to_monitor(monitor: MonitorRect, x: f64, y: f64) -> f64 {
    let nearest_x = x.clamp(
        f64::from(monitor.x),
        f64::from(rect_right(monitor.x, monitor.width)),
    );
    let nearest_y = y.clamp(
        f64::from(monitor.y),
        f64::from(rect_bottom(monitor.y, monitor.height)),
    );
    (x - nearest_x).hypot(y - nearest_y)
}

fn monitor_for_point(monitors: &[MonitorRect], x: f64, y: f64) -> Option<MonitorRect> {
    monitors
        .iter()
        .copied()
        .find(|monitor| contains_point(*monitor, x, y))
        .or_else(|| {
            monitors.iter().copied().min_by(|a, b| {
                distance_to_monitor(*a, x, y)
                    .partial_cmp(&distance_to_monitor(*b, x, y))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
        })
}

fn rect_intersection_area(bounds: WindowBounds, monitor: MonitorRect) -> i64 {
    let left = bounds.x.max(monitor.x);
    let top = bounds.y.max(monitor.y);
    let right = rect_right(bounds.x, bounds.width).min(rect_right(monitor.x, monitor.width));
    let bottom = rect_bottom(bounds.y, bounds.height).min(rect_bottom(monitor.y, monitor.height));
    let width = i64::from((right - left).max(0));
    let height = i64::from((bottom - top).max(0));
    width * height
}

fn monitor_for_bounds(bounds: WindowBounds, monitors: &[MonitorRect]) -> Option<MonitorRect> {
    let center_x = f64::from(bounds.x) + f64::from(bounds.width) / 2.0;
    let center_y = f64::from(bounds.y) + f64::from(bounds.height) / 2.0;
    monitors
        .iter()
        .copied()
        .find(|monitor| contains_point(*monitor, center_x, center_y))
        .or_else(|| {
            monitors
                .iter()
                .copied()
                .max_by_key(|monitor| rect_intersection_area(bounds, *monitor))
                .filter(|monitor| rect_intersection_area(bounds, *monitor) > 0)
        })
}

fn clamp_origin(value: i32, min: i32, container_size: u32, window_size: u32) -> i32 {
    if window_size >= container_size {
        return min;
    }
    let max = rect_right(min, container_size).saturating_sub(window_size as i32);
    value.clamp(min, max)
}

fn clamp_bounds_to_monitor(bounds: WindowBounds, monitor: MonitorRect) -> WindowBounds {
    let width = bounds.width.min(monitor.width).max(1);
    let height = bounds.height.min(monitor.height).max(1);
    WindowBounds {
        x: clamp_origin(bounds.x, monitor.x, monitor.width, width),
        y: clamp_origin(bounds.y, monitor.y, monitor.height, height),
        width,
        height,
    }
}

fn centered_bounds(size: PhysicalSize<u32>, monitor: MonitorRect) -> WindowBounds {
    let width = size.width.min(monitor.width).max(1);
    let height = size.height.min(monitor.height).max(1);
    WindowBounds {
        x: monitor.x + ((monitor.width - width) / 2) as i32,
        y: monitor.y + ((monitor.height - height) / 2) as i32,
        width,
        height,
    }
}

fn resolve_window_bounds(
    saved: Option<WindowBounds>,
    window_size: PhysicalSize<u32>,
    monitors: &[MonitorRect],
    cursor: Option<PhysicalPosition<f64>>,
    primary: Option<MonitorRect>,
) -> Option<WindowBounds> {
    let monitors: Vec<MonitorRect> = monitors
        .iter()
        .copied()
        .filter(|monitor| is_usable_monitor(*monitor))
        .collect();
    if monitors.is_empty() {
        return None;
    }

    if let Some(saved) = saved {
        if saved.width > 0 && saved.height > 0 {
            if let Some(monitor) = monitor_for_bounds(saved, &monitors) {
                return Some(clamp_bounds_to_monitor(saved, monitor));
            }
        }
    }

    if let Some(cursor) = cursor {
        if let Some(monitor) = monitor_for_point(&monitors, cursor.x, cursor.y) {
            return Some(centered_bounds(window_size, monitor));
        }
    }

    primary
        .filter(|monitor| is_usable_monitor(*monitor))
        .or_else(|| monitors.first().copied())
        .map(|monitor| centered_bounds(window_size, monitor))
}

fn current_window_size<R: Runtime>(window: &Window<R>) -> PhysicalSize<u32> {
    window.outer_size().unwrap_or(PhysicalSize {
        width: DEFAULT_WINDOW_WIDTH,
        height: DEFAULT_WINDOW_HEIGHT,
    })
}

fn available_monitor_rects<R: Runtime>(window: &Window<R>) -> Vec<MonitorRect> {
    window
        .available_monitors()
        .map(|monitors| monitors.iter().map(monitor_from_tauri).collect())
        .unwrap_or_default()
}

fn primary_monitor_rect<R: Runtime>(window: &Window<R>) -> Option<MonitorRect> {
    window
        .primary_monitor()
        .ok()
        .flatten()
        .map(|monitor| monitor_from_tauri(&monitor))
}

fn place_initial_window<R: Runtime>(
    window: &Window<R>,
    initial: Option<InitialPlacement>,
) -> Result<(), String> {
    let Some(initial) = initial else {
        return Ok(());
    };
    if window.is_maximized().map_err(|error| error.to_string())? {
        return Ok(());
    }
    // Retain offscreen coordinates until reveal, when the cursor's current
    // monitor is known. Move first so legacy frame conversion uses that DPI.
    window
        .set_position(PhysicalPosition::new(initial.bounds.x, initial.bounds.y))
        .map_err(|error| error.to_string())?;
    if initial.legacy_outer_size {
        let outer = window.outer_size().map_err(|error| error.to_string())?;
        let inner = window.inner_size().map_err(|error| error.to_string())?;
        window
            .set_size(client_size_for_outer_bounds(initial.bounds, outer, inner))
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

pub fn place_window_before_show<R: Runtime>(window: &Window<R>) -> Result<(), String> {
    if !is_managed_window_label(window.label())
        || window.is_maximized().map_err(|error| error.to_string())?
    {
        return Ok(());
    }
    // Hidden windows retain their native geometry. Do not restore stale disk
    // state or apply the title-bar thickness again on each reveal.
    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = current_window_size(window);
    let center = window
        .state::<WindowPlacementState>()
        .center_on_first_show
        .lock()
        .unwrap()
        .remove(window.label());
    place_bounds(
        window,
        (!center).then_some(WindowBounds {
            x: position.x,
            y: position.y,
            width: size.width,
            height: size.height,
        }),
        size,
    )
}

fn client_size_for_outer_bounds(
    bounds: WindowBounds,
    current_outer: PhysicalSize<u32>,
    current_inner: PhysicalSize<u32>,
) -> PhysicalSize<u32> {
    PhysicalSize::new(
        bounds
            .width
            .saturating_sub(current_outer.width.saturating_sub(current_inner.width))
            .max(1),
        bounds
            .height
            .saturating_sub(current_outer.height.saturating_sub(current_inner.height))
            .max(1),
    )
}

fn place_bounds<R: Runtime>(
    window: &Window<R>,
    saved: Option<WindowBounds>,
    size: PhysicalSize<u32>,
) -> Result<(), String> {
    let monitors = available_monitor_rects(window);
    let cursor = window.app_handle().cursor_position().ok();
    let primary = primary_monitor_rect(window);
    let Some(bounds) = resolve_window_bounds(saved, size, &monitors, cursor, primary) else {
        return Ok(());
    };

    let outer = window.outer_size().map_err(|error| error.to_string())?;
    if outer.width != bounds.width || outer.height != bounds.height {
        let inner = window.inner_size().map_err(|error| error.to_string())?;
        window
            .set_size(Size::Physical(client_size_for_outer_bounds(
                bounds, outer, inner,
            )))
            .map_err(|error| error.to_string())?;
    }
    window
        .set_position(Position::Physical(PhysicalPosition {
            x: bounds.x,
            y: bounds.y,
        }))
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test(start_paused = true)]
    async fn saves_only_after_the_latest_resize_has_settled() {
        let (sender, mut receiver) = watch::channel(());
        let waiting = tokio::spawn(async move { wait_for_idle(&mut receiver).await });
        sender.send_replace(());
        tokio::task::yield_now().await;
        tokio::time::advance(Duration::from_millis(400)).await;
        sender.send_replace(());
        tokio::task::yield_now().await;
        tokio::time::advance(Duration::from_millis(499)).await;
        assert!(!waiting.is_finished());
        tokio::time::advance(Duration::from_millis(1)).await;
        assert!(waiting.await.unwrap());
    }

    #[tokio::test(start_paused = true)]
    async fn dropping_save_requests_stops_the_worker() {
        let (sender, mut receiver) = watch::channel(());
        sender.send_replace(());
        drop(sender);
        assert!(!wait_for_idle(&mut receiver).await);
    }

    fn monitor(x: i32, y: i32, width: u32, height: u32) -> MonitorRect {
        MonitorRect {
            x,
            y,
            width,
            height,
        }
    }

    #[test]
    fn converts_legacy_outer_size_to_client_size_without_growth() {
        let bounds = WindowBounds {
            x: 10,
            y: 20,
            width: 1216,
            height: 839,
        };
        let inner = client_size_for_outer_bounds(
            bounds,
            PhysicalSize::new(1056, 759),
            PhysicalSize::new(1040, 720),
        );
        assert_eq!(inner, PhysicalSize::new(1200, 800));
        // A second hide/reveal with the same frame cannot add its thickness again.
        assert_eq!(
            client_size_for_outer_bounds(bounds, PhysicalSize::new(1216, 839), inner),
            inner
        );
    }

    #[test]
    fn new_plugin_state_takes_precedence_over_legacy_and_ignores_special_windows() {
        let legacy = parse_window_placements(Some(serde_json::json!({
            "chat": {"x": 1, "y": 2, "width": 1000, "height": 700},
            "voice-call": {"x": 3, "y": 4, "width": 400, "height": 600},
            "pet": {"x": 0, "y": 0, "width": 200, "height": 300}
        })))
        .unwrap();
        let current = parse_window_placements(Some(serde_json::json!({
            "chat": {"x": 10, "y": 20, "width": 1200, "height": 800, "maximized": true}
        })))
        .unwrap();
        let result = initial_placements(legacy, current);
        assert_eq!(result.len(), 2);
        assert!(!result["chat"].legacy_outer_size);
        assert_eq!(result["chat"].bounds.width, 1200);
        assert!(result["voice-call"].legacy_outer_size);
    }

    #[test]
    fn plugin_does_not_own_visibility_or_decorations() {
        assert!(
            state_flags().contains(StateFlags::SIZE | StateFlags::POSITION | StateFlags::MAXIMIZED)
        );
        assert!(!state_flags()
            .intersects(StateFlags::VISIBLE | StateFlags::DECORATIONS | StateFlags::FULLSCREEN));
        assert!(!is_managed_window_label("pet"));
        assert!(!is_managed_window_label("bubble"));
    }

    #[test]
    fn restores_saved_bounds_when_center_is_visible() {
        let bounds = resolve_window_bounds(
            Some(WindowBounds {
                x: 2000,
                y: 120,
                width: 700,
                height: 500,
            }),
            PhysicalSize {
                width: 720,
                height: 640,
            },
            &[monitor(0, 0, 1920, 1080), monitor(1920, 0, 1920, 1080)],
            None,
            None,
        )
        .expect("bounds");

        assert_eq!(bounds.x, 2000);
        assert_eq!(bounds.y, 120);
    }

    #[test]
    fn clamps_saved_bounds_inside_current_monitor() {
        let bounds = resolve_window_bounds(
            Some(WindowBounds {
                x: 1800,
                y: 900,
                width: 700,
                height: 500,
            }),
            PhysicalSize {
                width: 720,
                height: 640,
            },
            &[monitor(0, 0, 1920, 1080)],
            None,
            None,
        )
        .expect("bounds");

        assert_eq!(
            bounds,
            WindowBounds {
                x: 1220,
                y: 580,
                width: 700,
                height: 500,
            }
        );
    }

    #[test]
    fn centers_on_cursor_monitor_when_saved_bounds_are_offscreen() {
        let bounds = resolve_window_bounds(
            Some(WindowBounds {
                x: 4200,
                y: 0,
                width: 700,
                height: 500,
            }),
            PhysicalSize {
                width: 700,
                height: 500,
            },
            &[monitor(0, 0, 1920, 1080), monitor(1920, 0, 1920, 1080)],
            Some(PhysicalPosition {
                x: 2500.0,
                y: 300.0,
            }),
            None,
        )
        .expect("bounds");

        assert_eq!(bounds.x, 2530);
        assert_eq!(bounds.y, 290);
    }

    #[test]
    fn falls_back_to_primary_monitor() {
        let bounds = resolve_window_bounds(
            None,
            PhysicalSize {
                width: 700,
                height: 500,
            },
            &[monitor(1920, 0, 1920, 1080), monitor(0, 0, 1920, 1080)],
            None,
            Some(monitor(0, 0, 1920, 1080)),
        )
        .expect("bounds");

        assert_eq!(bounds.x, 610);
        assert_eq!(bounds.y, 290);
    }
}
