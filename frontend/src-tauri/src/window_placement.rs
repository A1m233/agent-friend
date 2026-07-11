use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use tauri::{
    AppHandle, Manager, PhysicalPosition, PhysicalSize, Position, Runtime, Size, WebviewWindow,
};
use tauri_plugin_store::StoreExt;

use crate::paths;

const WINDOW_PLACEMENTS_KEY: &str = "windowPlacements";
const MANAGED_WINDOW_LABELS: &[&str] = &[
    "chat",
    "settings",
    "im-connect",
    "voice-call",
    "memory-inspector",
    "live2d-debugger",
];
const DEFAULT_WINDOW_WIDTH: u32 = 720;
const DEFAULT_WINDOW_HEIGHT: u32 = 640;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
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

fn save_window_placements<R: Runtime>(
    app: &AppHandle<R>,
    placements: WindowPlacementMap,
) -> Result<(), String> {
    let store = app
        .store(paths::UI_STATE_STORE_PATH)
        .map_err(|e| e.to_string())?;
    store.set(
        WINDOW_PLACEMENTS_KEY,
        serde_json::to_value(placements).map_err(|e| e.to_string())?,
    );
    store.save().map_err(|e| e.to_string())
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

fn current_window_size<R: Runtime>(window: &WebviewWindow<R>) -> PhysicalSize<u32> {
    window.outer_size().unwrap_or(PhysicalSize {
        width: DEFAULT_WINDOW_WIDTH,
        height: DEFAULT_WINDOW_HEIGHT,
    })
}

fn available_monitor_rects<R: Runtime>(window: &WebviewWindow<R>) -> Vec<MonitorRect> {
    window
        .available_monitors()
        .map(|monitors| monitors.iter().map(monitor_from_tauri).collect())
        .unwrap_or_default()
}

fn primary_monitor_rect<R: Runtime>(window: &WebviewWindow<R>) -> Option<MonitorRect> {
    window
        .primary_monitor()
        .ok()
        .flatten()
        .map(|monitor| monitor_from_tauri(&monitor))
}

pub fn place_window_before_show<R: Runtime>(
    app: &AppHandle<R>,
    window: &WebviewWindow<R>,
) -> Result<(), String> {
    let label = window.label();
    if !is_managed_window_label(label) {
        return Ok(());
    }

    let placements = load_window_placements(app)?;
    let saved = placements.get(label).copied();
    let monitors = available_monitor_rects(window);
    let cursor = app.cursor_position().ok();
    let primary = primary_monitor_rect(window);
    let Some(bounds) = resolve_window_bounds(
        saved,
        current_window_size(window),
        &monitors,
        cursor,
        primary,
    ) else {
        return Ok(());
    };

    if let Err(e) = window.set_size(Size::Physical(PhysicalSize {
        width: bounds.width,
        height: bounds.height,
    })) {
        log::warn!("window placement: set_size failed for {label}: {e}");
    }
    if let Err(e) = window.set_position(Position::Physical(PhysicalPosition {
        x: bounds.x,
        y: bounds.y,
    })) {
        log::warn!("window placement: set_position failed for {label}: {e}");
    }
    Ok(())
}

pub fn remember_window_placement<R: Runtime>(window: &WebviewWindow<R>) -> Result<(), String> {
    let label = window.label();
    if !is_managed_window_label(label) {
        return Ok(());
    }

    let position = window.outer_position().map_err(|e| e.to_string())?;
    let size = window.outer_size().map_err(|e| e.to_string())?;
    if size.width == 0 || size.height == 0 {
        return Ok(());
    }

    let app = window.app_handle().clone();
    let mut placements = load_window_placements(&app)?;
    placements.insert(
        label.to_string(),
        WindowBounds {
            x: position.x,
            y: position.y,
            width: size.width,
            height: size.height,
        },
    );
    save_window_placements(&app, placements)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn monitor(x: i32, y: i32, width: u32, height: u32) -> MonitorRect {
        MonitorRect {
            x,
            y,
            width,
            height,
        }
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
