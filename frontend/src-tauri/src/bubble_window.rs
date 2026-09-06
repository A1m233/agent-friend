//! Native application of the pet-owned accessory layout; bubble remains an independent window.
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
#[cfg(not(target_os = "windows"))]
use tauri::PhysicalSize;
use tauri::{AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, WebviewWindow};
use tokio::sync::Notify;

pub const BUBBLE_LABEL: &str = "bubble";
pub const MIN_W: u32 = 240;
pub const MIN_H: u32 = 64;
pub const MAX_W: u32 = 360;
pub const MAX_H: u32 = 480;
const MEASUREMENT_EVENT: &str = "pet://bubble-measurement";
const PRESENTATION_EVENT: &str = "bubble://layout-applied";
static NEXT_LAYOUT_OWNER: AtomicU64 = AtomicU64::new(1);
static NEXT_PRESENTATION_REVISION: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PhysicalRect {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}
impl PhysicalRect {
    fn union(self, other: Self) -> Option<Self> {
        let x = self.x.min(other.x);
        let y = self.y.min(other.y);
        let right = (i64::from(self.x) + i64::from(self.width))
            .max(i64::from(other.x) + i64::from(other.width));
        let bottom = (i64::from(self.y) + i64::from(self.height))
            .max(i64::from(other.y) + i64::from(other.height));
        let width = u32::try_from(right - i64::from(x)).ok()?;
        let height = u32::try_from(bottom - i64::from(y)).ok()?;
        (width <= i32::MAX as u32 && height <= i32::MAX as u32).then_some(Self {
            x,
            y,
            width,
            height,
        })
    }
    fn contains(self, other: Self) -> bool {
        other.width > 0
            && other.height > 0
            && other.x >= self.x
            && other.y >= self.y
            && i64::from(other.x) + i64::from(other.width)
                <= i64::from(self.x) + i64::from(self.width)
            && i64::from(other.y) + i64::from(other.height)
                <= i64::from(self.y) + i64::from(self.height)
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BubbleMeasurement {
    revision: u64,
    width_physical: u32,
    height_physical: u32,
    desired_width_physical: u32,
    desired_height_physical: u32,
    scale_factor: f64,
    show_requested: bool,
    retain_bounds: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BubblePresentationState {
    pub state_revision: u64,
    pub layout_owner: u64,
    pub visible: bool,
    pub applied: bool,
    pub measurement_revision: u64,
    pub layout_revision: u64,
    pub width_css: f64,
    pub height_css: f64,
    pub host_rect_physical: Option<PhysicalRect>,
    pub target_rect_physical: Option<PhysicalRect>,
    pub scale_factor: f64,
    pub retaining: bool,
    pub carrier_plan: Option<BubbleCarrierPlan>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BubbleCarrierPlan {
    owner: u64,
    generation: u64,
    layout_revision: u64,
    measurement_revision: u64,
    rect_physical: PhysicalRect,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccessoryContext {
    generation: u64,
    origin: PhysicalPosition<i32>,
    monitors: Vec<crate::pet_display_topology::MonitorSnapshot>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccessoryLayoutSession {
    owner: u64,
    context: AccessoryContext,
    measurement: BubbleMeasurement,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AccessoryLayoutRequest {
    owner: u64,
    generation: u64,
    revision: u64,
    measurement_revision: u64,
    bubble: Option<PhysicalRect>,
    retain_bounds: bool,
}

#[derive(Clone, Copy, Debug)]
struct Carrier {
    owner: u64,
    generation: u64,
    measurement_revision: u64,
    rect: PhysicalRect,
    scale: f64,
}

#[derive(Debug)]
struct LayoutState {
    owner: u64,
    last_revision: u64,
    measurement_revision: u64,
    desired: (u32, u32),
    actual: (u32, u32),
    actual_position: (i32, i32),
    scale: f64,
    applied: Option<AccessoryLayoutRequest>,
    carrier: Option<Carrier>,
    pending_carrier: Option<BubbleCarrierPlan>,
    show_requested: bool,
    retain_bounds: bool,
    retaining: bool,
}
impl Default for LayoutState {
    fn default() -> Self {
        Self {
            owner: 0,
            last_revision: 0,
            measurement_revision: 1,
            desired: (280, 96),
            actual: (280, 96),
            actual_position: (0, 0),
            scale: 1.0,
            applied: None,
            carrier: None,
            pending_carrier: None,
            show_requested: false,
            retain_bounds: false,
            retaining: false,
        }
    }
}
impl LayoutState {
    fn measurement(&self) -> BubbleMeasurement {
        BubbleMeasurement {
            revision: self.measurement_revision,
            width_physical: self.actual.0,
            height_physical: self.actual.1,
            desired_width_physical: (f64::from(self.desired.0) * self.scale).round().max(1.0)
                as u32,
            desired_height_physical: (f64::from(self.desired.1) * self.scale).round().max(1.0)
                as u32,
            scale_factor: self.scale,
            show_requested: self.show_requested,
            retain_bounds: self.retain_bounds,
        }
    }
    fn accepts(&self, request: &AccessoryLayoutRequest, generation: u64) -> bool {
        request.owner == self.owner
            && request.owner != 0
            && request.generation == generation
            && request.revision > self.last_revision
            && request.measurement_revision == self.measurement_revision
    }
    fn actual_rect(&self) -> PhysicalRect {
        PhysicalRect {
            x: self.actual_position.0,
            y: self.actual_position.1,
            width: self.actual.0,
            height: self.actual.1,
        }
    }
    // A newer content measurement invalidates its receipt, not the last valid
    // screen surface. This keeps an exiting DOM alive while the pet computes T.
    fn carrier_valid_for(&self, generation: u64) -> bool {
        self.carrier.is_some_and(|carrier| {
            carrier.owner == self.owner
                && carrier.owner != 0
                && carrier.generation == generation
                && (carrier.scale - self.scale).abs() <= 0.001
                && carrier.rect == self.actual_rect()
        })
    }
    fn applied_for(&self, generation: u64) -> bool {
        if self.pending_carrier.is_some() || !self.carrier_valid_for(generation) {
            return false;
        }
        self.applied.as_ref().is_some_and(|r| {
            r.owner == self.owner
                && r.generation == generation
                && r.measurement_revision == self.measurement_revision
                && self.carrier.is_some_and(|carrier| {
                    carrier.measurement_revision == r.measurement_revision
                        && r.bubble.is_some_and(|target| {
                            if self.retaining {
                                carrier.rect.contains(target)
                            } else {
                                carrier.rect == target
                            }
                        })
                })
        })
    }
    fn set_request(&mut self, desired: (u32, u32), retain_bounds: bool) {
        if self.desired != desired || self.retain_bounds != retain_bounds {
            self.desired = desired;
            self.retain_bounds = retain_bounds;
            self.measurement_revision = self.measurement_revision.saturating_add(1);
            self.pending_carrier = None;
        }
    }
    fn can_retain(&self, request: &AccessoryLayoutRequest, generation: u64, visible: bool) -> bool {
        self.retain_bounds
            && request.retain_bounds
            && self.show_requested
            && visible
            && request.owner == self.owner
            && request.generation == generation
            && self.carrier_valid_for(generation)
    }
    fn planned_carrier(
        &self,
        request: &AccessoryLayoutRequest,
        generation: u64,
        visible: bool,
    ) -> Option<PhysicalRect> {
        request.bubble.map(|target| {
            if self.can_retain(request, generation, visible) && self.actual_rect().contains(target)
            {
                self.actual_rect()
            } else {
                target
            }
        })
    }
    fn growth_plan(
        &self,
        request: &AccessoryLayoutRequest,
        generation: u64,
        visible: bool,
        work_areas: &[PhysicalRect],
    ) -> Option<BubbleCarrierPlan> {
        if !self.can_retain(request, generation, visible) {
            return None;
        }
        let target = request.bubble?;
        let host = self.actual_rect();
        if host.contains(target) {
            return None;
        }
        let rect_physical = host.union(target)?;
        // A union across monitors could cover a taskbar or a gap between screens.
        if !work_areas.iter().any(|area| area.contains(rect_physical)) {
            return None;
        }
        Some(BubbleCarrierPlan {
            owner: request.owner,
            generation,
            layout_revision: request.revision,
            measurement_revision: request.measurement_revision,
            rect_physical,
        })
    }
    fn prepare_carrier(&mut self, request: AccessoryLayoutRequest, plan: BubbleCarrierPlan) {
        self.commit(request, Some(self.actual_rect()));
        self.pending_carrier = Some(plan);
    }
    fn accepts_carrier(&self, plan: &BubbleCarrierPlan, generation: u64) -> bool {
        self.pending_carrier.as_ref() == Some(plan)
            && plan.owner == self.owner
            && plan.generation == generation
            && plan.layout_revision == self.last_revision
            && plan.measurement_revision == self.measurement_revision
            && self.show_requested
            && self.retain_bounds
            && self.carrier_valid_for(generation)
            && self.applied.as_ref().is_some_and(|request| {
                request.retain_bounds
                    && request.owner == plan.owner
                    && request.generation == plan.generation
                    && request.revision == plan.layout_revision
                    && request.measurement_revision == plan.measurement_revision
                    && request
                        .bubble
                        .is_some_and(|target| plan.rect_physical.contains(target))
            })
    }
    fn commit(&mut self, request: AccessoryLayoutRequest, carrier: Option<PhysicalRect>) {
        self.retaining = carrier.is_some()
            && (carrier != request.bubble
                || (self.retaining && self.retain_bounds && request.retain_bounds));
        self.pending_carrier = None;
        self.carrier = carrier.map(|rect| Carrier {
            owner: request.owner,
            generation: request.generation,
            measurement_revision: request.measurement_revision,
            rect,
            scale: self.scale,
        });
        self.last_revision = request.revision;
        self.applied = Some(request);
    }
    fn refresh_actual(&mut self, rect: PhysicalRect, scale: f64) -> bool {
        let scale_changed = (scale - self.scale).abs() > 0.001;
        let changed = self.actual_rect() != rect || scale_changed;
        let displaced = self.carrier.is_some_and(|carrier| carrier.rect != rect);
        if scale_changed || displaced {
            self.measurement_revision = self.measurement_revision.saturating_add(1);
            self.applied = None;
            self.carrier = None;
            self.pending_carrier = None;
            self.retaining = false;
        }
        self.actual = (rect.width, rect.height);
        self.actual_position = (rect.x, rect.y);
        self.scale = scale;
        changed
    }
}

#[derive(Clone, Default)]
pub struct BubbleState {
    pub is_visible: Arc<AtomicBool>,
    inner: Arc<Mutex<LayoutState>>,
    notify: Arc<Notify>,
}
fn native_window(app: &AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window(BUBBLE_LABEL)
        .ok_or_else(|| "bubble window is unavailable".to_string())
}

// Native reads and the state mutation run on the same thread as bounds changes.
// Never hold the state mutex while waiting for a queued main-thread operation.
async fn on_native_state<T, F>(
    app: &AppHandle,
    state: &BubbleState,
    operation: F,
) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce(&AppHandle, &WebviewWindow, &BubbleState, &mut LayoutState) -> Result<T, String>
        + Send
        + 'static,
{
    let window = native_window(app)?;
    let scheduler = window.clone();
    let app = app.clone();
    let state = state.clone();
    let (result_tx, result_rx) = tokio::sync::oneshot::channel();
    scheduler
        .run_on_main_thread(move || {
            let result = state
                .inner
                .lock()
                .map_err(|error| error.to_string())
                .and_then(|mut inner| operation(&app, &window, &state, &mut inner));
            let _ = result_tx.send(result);
        })
        .map_err(|error| error.to_string())?;
    result_rx
        .await
        .map_err(|_| "bubble native operation was cancelled".to_string())?
}

#[cfg(target_os = "windows")]
fn set_native_bounds(window: &WebviewWindow, rect: PhysicalRect) -> Result<(), String> {
    use windows::Win32::UI::WindowsAndMessaging::{SetWindowPos, SWP_NOACTIVATE, SWP_NOZORDER};
    let hwnd = window.hwnd().map_err(|error| error.to_string())?;
    let width = i32::try_from(rect.width).map_err(|_| "bubble width exceeds native range")?;
    let height = i32::try_from(rect.height).map_err(|_| "bubble height exceeds native range")?;
    // One geometry operation avoids a shrunken frame at the previous origin.
    // The caller is already on the window thread; no async-position flag is used.
    unsafe {
        SetWindowPos(
            hwnd,
            None,
            rect.x,
            rect.y,
            width,
            height,
            SWP_NOACTIVATE | SWP_NOZORDER,
        )
    }
    .map_err(|error| error.to_string())
}

#[cfg(not(target_os = "windows"))]
fn set_native_bounds(window: &WebviewWindow, rect: PhysicalRect) -> Result<(), String> {
    // Tauri does not expose a combined bounds operation on these platforms.
    // Preserve the existing path; WebView/compositor paint timing is not atomic.
    window
        .set_size(PhysicalSize::new(rect.width, rect.height))
        .map_err(|error| error.to_string())?;
    window
        .set_position(PhysicalPosition::new(rect.x, rect.y))
        .map_err(|error| error.to_string())
}

fn topology_generation(app: &AppHandle) -> u64 {
    app.try_state::<crate::PetOverlayState>()
        .and_then(|state| {
            state
                .inner
                .lock()
                .ok()
                .map(|guard| guard.topology_epoch.generation())
        })
        .unwrap_or(0)
}
fn refresh_measurement(window: &WebviewWindow, inner: &mut LayoutState) -> Result<bool, String> {
    let actual = window.outer_size().map_err(|e| e.to_string())?;
    let position = window.outer_position().map_err(|e| e.to_string())?;
    let scale = window.scale_factor().map_err(|e| e.to_string())?;
    let scale = if scale.is_finite() && scale > 0.0 {
        scale
    } else {
        1.0
    };
    Ok(inner.refresh_actual(
        PhysicalRect {
            x: position.x,
            y: position.y,
            width: actual.width,
            height: actual.height,
        },
        scale,
    ))
}
fn presentation(
    app: &AppHandle,
    window: &WebviewWindow,
    inner: &LayoutState,
) -> BubblePresentationState {
    let generation = topology_generation(app);
    BubblePresentationState {
        state_revision: NEXT_PRESENTATION_REVISION.fetch_add(1, Ordering::Relaxed),
        layout_owner: inner.owner,
        visible: window.is_visible().unwrap_or(false),
        applied: inner.applied_for(generation),
        measurement_revision: inner.measurement_revision,
        layout_revision: inner.last_revision,
        width_css: f64::from(inner.actual.0) / inner.scale,
        height_css: f64::from(inner.actual.1) / inner.scale,
        host_rect_physical: Some(inner.actual_rect()),
        target_rect_physical: inner
            .applied
            .as_ref()
            .filter(|request| request.owner == inner.owner && request.generation == generation)
            .and_then(|request| request.bubble),
        scale_factor: inner.scale,
        retaining: inner.retaining && inner.carrier_valid_for(generation),
        carrier_plan: inner
            .pending_carrier
            .filter(|plan| inner.accepts_carrier(plan, generation)),
    }
}
fn emit_presentation(app: &AppHandle, value: &BubblePresentationState) {
    let _ = app.emit_to(BUBBLE_LABEL, PRESENTATION_EVENT, value);
}
fn show_without_focus(window: &WebviewWindow) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        use objc2::{msg_send, runtime::AnyObject};
        let ptr = window.ns_window().map_err(|e| e.to_string())?;
        if !ptr.is_null() {
            let native: &AnyObject = unsafe { &*(ptr as *const AnyObject) };
            unsafe {
                let _: () = msg_send![native, orderFrontRegardless];
            }
            return Ok(());
        }
    }
    window.show().map_err(|e| e.to_string())
}
fn reconcile_visibility(
    app: &AppHandle,
    window: &WebviewWindow,
    state: &BubbleState,
    inner: &LayoutState,
) -> Result<(), String> {
    let should_show = inner.show_requested && inner.carrier_valid_for(topology_generation(app));
    let visible = window.is_visible().unwrap_or(false);
    if should_show && !visible {
        show_without_focus(window)?;
    }
    if !should_show && visible {
        window.hide().map_err(|e| e.to_string())?;
    }
    state.is_visible.store(should_show, Ordering::Release);
    Ok(())
}

pub fn init(app: &AppHandle) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window(BUBBLE_LABEL) {
        let _ = window.set_size(LogicalSize::new(280.0, 96.0));
        crate::apply_floating_window_level(&window);
    }
    let state = app.state::<BubbleState>().inner().clone();
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        loop {
            let requested = state
                .inner
                .lock()
                .map(|inner| inner.show_requested)
                .unwrap_or(false);
            if !requested {
                state.notify.notified().await;
                continue;
            }
            let _ = on_native_state(&handle, &state, |app, window, state, inner| {
                if refresh_measurement(window, inner)? {
                    let _ = app.emit_to("pet", MEASUREMENT_EVENT, inner.measurement());
                }
                let was_visible = state.is_visible.load(Ordering::Acquire);
                reconcile_visibility(app, window, state, inner)?;
                if was_visible != state.is_visible.load(Ordering::Acquire) {
                    emit_presentation(app, &presentation(app, window, inner));
                }
                Ok(())
            })
            .await;
            tokio::time::sleep(Duration::from_millis(16)).await;
        }
    });
    Ok(())
}

#[tauri::command]
pub async fn get_pet_accessory_context(app: AppHandle) -> Result<AccessoryContext, String> {
    let pet = app
        .get_webview_window("pet")
        .ok_or("pet window is unavailable")?;
    let snapshot =
        crate::read_pet_display_topology(&pet).ok_or("monitor topology is unavailable")?;
    Ok(AccessoryContext {
        generation: topology_generation(&app),
        origin: pet.outer_position().map_err(|e| e.to_string())?,
        monitors: snapshot.monitors,
    })
}
#[tauri::command]
pub async fn begin_pet_accessory_layout(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
) -> Result<AccessoryLayoutSession, String> {
    let context = get_pet_accessory_context(app.clone()).await?;
    on_native_state(&app, state.inner(), move |app, window, state, inner| {
        inner.owner = NEXT_LAYOUT_OWNER.fetch_add(1, Ordering::Relaxed);
        inner.last_revision = 0;
        inner.applied = None;
        inner.carrier = None;
        inner.pending_carrier = None;
        inner.retaining = false;
        refresh_measurement(window, inner)?;
        reconcile_visibility(app, window, state, inner)?;
        Ok(AccessoryLayoutSession {
            owner: inner.owner,
            context,
            measurement: inner.measurement(),
        })
    })
    .await
}
#[tauri::command]
pub async fn set_bubble_size(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
    width: u32,
    height: u32,
    retain_bounds: Option<bool>,
) -> Result<BubbleMeasurement, String> {
    on_native_state(&app, state.inner(), move |app, window, state, inner| {
        refresh_measurement(window, inner)?;
        inner.set_request(
            clamp_bubble_size(width, height),
            retain_bounds.unwrap_or(false),
        );
        // Keep the previous legal carrier visible while waiting for the pet's
        // target. The new measurement cannot qualify a receipt before apply.
        reconcile_visibility(app, window, state, inner)?;
        let measurement = inner.measurement();
        app.emit_to("pet", MEASUREMENT_EVENT, &measurement)
            .map_err(|e| e.to_string())?;
        emit_presentation(app, &presentation(app, window, inner));
        Ok(measurement)
    })
    .await
}
#[tauri::command]
pub async fn apply_pet_accessory_layout(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
    request: AccessoryLayoutRequest,
) -> Result<BubblePresentationState, String> {
    on_native_state(&app, state.inner(), move |app, window, state, inner| {
        refresh_measurement(window, inner)?;
        let generation = topology_generation(app);
        // Validate on the actual window thread, immediately before mutating it.
        if !inner.accepts(&request, generation) {
            let _ = app.emit_to("pet", MEASUREMENT_EVENT, inner.measurement());
            return Ok(presentation(app, window, inner));
        }
        if request.bubble.is_some_and(|rect| {
            rect.width == 0
                || rect.height == 0
                || rect.width > i32::MAX as u32
                || rect.height > i32::MAX as u32
        }) {
            return Err("invalid bubble layout size".to_string());
        }
        let visible = window.is_visible().unwrap_or(false);
        let work_areas = if inner.can_retain(&request, generation, visible) {
            app.get_webview_window("pet")
                .and_then(|pet| crate::read_pet_display_topology(&pet))
                .map(|snapshot| {
                    snapshot
                        .monitors
                        .into_iter()
                        .map(|monitor| PhysicalRect {
                            x: monitor.work_area.x,
                            y: monitor.work_area.y,
                            width: monitor.work_area.width,
                            height: monitor.work_area.height,
                        })
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default()
        } else {
            Vec::new()
        };
        if let Some(plan) = inner.growth_plan(&request, generation, visible, &work_areas) {
            // Publish the target before expanding the native host. The bubble
            // installs viewport compensation and echoes this exact plan to commit.
            inner.prepare_carrier(request, plan);
        } else {
            let carrier = inner.planned_carrier(&request, generation, visible);
            if let Some(rect) = carrier {
                if inner.actual_rect() != rect {
                    set_native_bounds(window, rect)?;
                }
            }
            inner.commit(request, carrier);
        }
        let changed = refresh_measurement(window, inner)?;
        reconcile_visibility(app, window, state, inner)?;
        if changed {
            let _ = app.emit_to("pet", MEASUREMENT_EVENT, inner.measurement());
        }
        let value = presentation(app, window, inner);
        emit_presentation(app, &value);
        Ok(value)
    })
    .await
}
#[tauri::command]
pub async fn commit_bubble_carrier(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
    plan: BubbleCarrierPlan,
) -> Result<BubblePresentationState, String> {
    on_native_state(&app, state.inner(), move |app, window, state, inner| {
        refresh_measurement(window, inner)?;
        if !inner.accepts_carrier(&plan, topology_generation(app)) {
            return Ok(presentation(app, window, inner));
        }
        let request = inner
            .applied
            .clone()
            .ok_or("bubble carrier target is unavailable")?;
        set_native_bounds(window, plan.rect_physical)?;
        inner.commit(request, Some(plan.rect_physical));
        inner.retaining = true;
        let changed = refresh_measurement(window, inner)?;
        reconcile_visibility(app, window, state, inner)?;
        if changed {
            let _ = app.emit_to("pet", MEASUREMENT_EVENT, inner.measurement());
        }
        let value = presentation(app, window, inner);
        emit_presentation(app, &value);
        Ok(value)
    })
    .await
}
#[tauri::command]
pub async fn show_bubble(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
) -> Result<BubblePresentationState, String> {
    on_native_state(&app, state.inner(), |app, window, state, inner| {
        inner.show_requested = true;
        refresh_measurement(window, inner)?;
        reconcile_visibility(app, window, state, inner)?;
        state.notify.notify_one();
        let _ = app.emit_to("pet", MEASUREMENT_EVENT, inner.measurement());
        let value = presentation(app, window, inner);
        emit_presentation(app, &value);
        Ok(value)
    })
    .await
}
#[tauri::command]
pub async fn hide_bubble(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
) -> Result<(), String> {
    on_native_state(&app, state.inner(), |app, window, state, inner| {
        if inner.show_requested || inner.retain_bounds || inner.carrier.is_some() {
            inner.measurement_revision = inner.measurement_revision.saturating_add(1);
        }
        inner.show_requested = false;
        inner.retain_bounds = false;
        inner.retaining = false;
        inner.carrier = None;
        inner.pending_carrier = None;
        inner.applied = None;
        let _ = app.emit_to("pet", MEASUREMENT_EVENT, inner.measurement());
        reconcile_visibility(app, window, state, inner)?;
        emit_presentation(app, &presentation(app, window, inner));
        Ok(())
    })
    .await
}
#[tauri::command]
pub async fn get_bubble_presentation_state(
    app: AppHandle,
    state: tauri::State<'_, BubbleState>,
) -> Result<BubblePresentationState, String> {
    on_native_state(&app, state.inner(), |app, window, _, inner| {
        if refresh_measurement(window, inner)? {
            let _ = app.emit_to("pet", MEASUREMENT_EVENT, inner.measurement());
        }
        Ok(presentation(app, window, inner))
    })
    .await
}
pub(crate) fn clamp_bubble_size(w: u32, h: u32) -> (u32, u32) {
    (w.clamp(MIN_W, MAX_W), h.clamp(MIN_H, MAX_H))
}
#[cfg(debug_assertions)]
#[tauri::command]
pub fn inject_test_envelope(app: AppHandle, text: String) -> Result<(), String> {
    emit_test_envelope(&app, text, "cron:bedtime")
}
#[cfg(debug_assertions)]
#[tauri::command]
pub fn inject_test_reminder_envelope(app: AppHandle, text: String) -> Result<(), String> {
    emit_test_envelope(&app, text, "todo_reminder")
}
#[cfg(debug_assertions)]
fn emit_test_envelope(app: &AppHandle, text: String, source_kind: &str) -> Result<(), String> {
    static NEXT_DEV_SEQUENCE: AtomicU64 = AtomicU64::new(1);
    let seq = NEXT_DEV_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    app.emit_to(
        BUBBLE_LABEL,
        "agent://push",
        build_test_envelope(&text, source_kind, seq),
    )
    .map_err(|e| e.to_string())
}
#[cfg(debug_assertions)]
fn build_test_envelope(text: &str, source_kind: &str, seq: u64) -> serde_json::Value {
    serde_json::json!({"kind":"agent_turn", "session_id":"dev-test", "seq":seq, "source_kind":source_kind, "events":[{"type":"text_delta", "text":text}]})
}
#[cfg(test)]
mod tests {
    use super::*;
    fn request() -> AccessoryLayoutRequest {
        AccessoryLayoutRequest {
            owner: 2,
            generation: 4,
            revision: 8,
            measurement_revision: 3,
            retain_bounds: false,
            bubble: Some(PhysicalRect {
                x: -200,
                y: 30,
                width: 400,
                height: 100,
            }),
        }
    }
    fn committed_state(rect: PhysicalRect, scale: f64) -> LayoutState {
        let mut state = LayoutState {
            owner: 2,
            measurement_revision: 3,
            actual: (rect.width, rect.height),
            actual_position: (rect.x, rect.y),
            scale,
            show_requested: true,
            ..Default::default()
        };
        state.commit(
            AccessoryLayoutRequest {
                bubble: Some(rect),
                ..request()
            },
            Some(rect),
        );
        state
    }
    fn next_request(
        state: &LayoutState,
        target: Option<PhysicalRect>,
        retain_bounds: bool,
    ) -> AccessoryLayoutRequest {
        AccessoryLayoutRequest {
            owner: state.owner,
            generation: 4,
            revision: state.last_revision + 1,
            measurement_revision: state.measurement_revision,
            bubble: target,
            retain_bounds,
        }
    }
    #[test]
    fn rejects_old_owner_topology_revision_and_measurement() {
        let state = LayoutState {
            owner: 2,
            last_revision: 7,
            measurement_revision: 3,
            ..Default::default()
        };
        assert!(state.accepts(&request(), 4));
        for stale in [
            AccessoryLayoutRequest {
                owner: 1,
                ..request()
            },
            AccessoryLayoutRequest {
                generation: 3,
                ..request()
            },
            AccessoryLayoutRequest {
                revision: 7,
                ..request()
            },
            AccessoryLayoutRequest {
                measurement_revision: 2,
                ..request()
            },
        ] {
            assert!(!state.accepts(&stale, 4));
        }
    }
    #[test]
    fn presentation_requires_matching_native_size_and_current_layout() {
        let mut state = committed_state(request().bubble.unwrap(), 1.0);
        assert!(state.applied_for(4));
        assert!(!state.applied_for(5));
        state.actual_position = (-100, 30);
        assert!(!state.applied_for(4));
        state.actual_position = (-200, 30);
        state.actual = (400, 200);
        assert!(!state.applied_for(4));
        state.actual = (400, 100);
        state.measurement_revision += 1;
        assert!(!state.applied_for(4));
    }
    #[test]
    fn content_or_retention_change_keeps_carrier_but_requires_a_new_receipt_layout() {
        let host = request().bubble.unwrap();
        let mut state = committed_state(host, 1.0);
        let desired = state.desired;
        let initial_revision = state.measurement_revision;
        state.set_request(desired, true);
        assert_eq!(state.measurement_revision, initial_revision + 1);
        assert!(state.measurement().retain_bounds);
        assert!(state.carrier_valid_for(4));
        assert!(!state.applied_for(4));
        state.set_request(desired, true);
        assert_eq!(state.measurement_revision, initial_revision + 1);
        state.set_request((360, 80), true);
        assert_eq!(state.measurement_revision, initial_revision + 2);
        assert!(state.carrier_valid_for(4));
        state.set_request((360, 80), false);
        assert_eq!(state.measurement_revision, initial_revision + 3);
        assert!(!state.measurement().retain_bounds);
    }
    #[test]
    fn contained_shrink_keeps_the_screen_carrier_for_every_direction_and_clamp() {
        let host = PhysicalRect {
            x: -200,
            y: 30,
            width: 400,
            height: 400,
        };
        for target in [
            PhysicalRect {
                y: 230,
                height: 200,
                ..host
            }, // top: keep lower edge
            PhysicalRect {
                height: 200,
                ..host
            }, // bottom: keep upper edge
            PhysicalRect {
                x: 0,
                y: 130,
                width: 200,
                height: 200,
            }, // left + centered
            PhysicalRect {
                y: 130,
                width: 200,
                height: 200,
                ..host
            }, // right + centered
            PhysicalRect {
                width: 240,
                height: 100,
                ..host
            }, // clamped screen corner
        ] {
            let mut state = committed_state(host, 1.0);
            state.set_request((target.width, target.height), true);
            let request = next_request(&state, Some(target), true);
            assert!(state.accepts(&request, 4));
            let carrier = state.planned_carrier(&request, 4, true);
            assert_eq!(carrier, Some(host));
            state.commit(request, carrier);
            assert!(!state.refresh_actual(host, 1.0));
            assert!(state.retaining);
            assert!(state.applied_for(4));
            assert_eq!(state.applied.as_ref().unwrap().bubble, Some(target));
        }
    }
    #[test]
    fn release_reclaims_the_target_and_stale_requests_cannot_retain_or_reclaim_it() {
        let host = PhysicalRect {
            x: 300,
            y: 100,
            width: 360,
            height: 480,
        };
        let target = PhysicalRect {
            y: 380,
            height: 200,
            ..host
        };
        let mut state = committed_state(host, 1.0);
        state.set_request((360, 200), true);
        let held = next_request(&state, Some(target), true);
        state.commit(held.clone(), Some(host));
        assert!(state.retaining);
        state.set_request((360, 200), false);
        assert!(state.carrier_valid_for(4));
        assert!(!state.applied_for(4));
        assert!(!state.accepts(&held, 4));
        let release = next_request(&state, Some(target), false);
        let carrier = state.planned_carrier(&release, 4, true);
        assert_eq!(carrier, Some(target));
        state.commit(release.clone(), carrier);
        state.refresh_actual(target, 1.0);
        assert!(!state.retaining);
        assert!(state.applied_for(4));
        assert!(!state.accepts(&release, 4));
        state.set_request((360, 300), true);
        assert!(!state.accepts(&release, 4));
    }
    #[test]
    fn drag_hidden_or_invalid_context_bypass_retention_and_growth_needs_a_plan() {
        let host = PhysicalRect {
            x: 200,
            y: 200,
            width: 360,
            height: 400,
        };
        let contained = PhysicalRect {
            y: 220,
            height: 200,
            ..host
        };
        let mut state = committed_state(host, 1.0);
        state.set_request((360, 200), true);
        let held = next_request(&state, Some(contained), true);
        let drag = AccessoryLayoutRequest {
            retain_bounds: false,
            ..held.clone()
        };
        assert_eq!(state.planned_carrier(&drag, 4, true), Some(contained));
        assert_eq!(state.planned_carrier(&held, 4, false), Some(contained));
        assert_eq!(state.planned_carrier(&held, 5, true), Some(contained));
        for target in [
            PhysicalRect {
                y: 190,
                ..contained
            },
            PhysicalRect {
                height: 500,
                ..host
            },
        ] {
            let request = next_request(&state, Some(target), true);
            assert_eq!(state.planned_carrier(&request, 4, true), Some(target));
        }
        state.owner += 1;
        let new_owner = next_request(&state, Some(contained), true);
        assert_eq!(state.planned_carrier(&new_owner, 4, true), Some(contained));
    }
    fn growth_fixture() -> (LayoutState, AccessoryLayoutRequest, PhysicalRect) {
        let host = PhysicalRect {
            x: -600,
            y: 300,
            width: 450,
            height: 400,
        };
        let target = PhysicalRect {
            y: 100,
            height: 600,
            ..host
        };
        let area = PhysicalRect {
            x: -1920,
            y: 0,
            width: 1920,
            height: 1040,
        };
        let mut state = committed_state(host, 1.25);
        state.set_request((360, 480), true);
        let request = next_request(&state, Some(target), true);
        (state, request, area)
    }
    #[test]
    fn growth_keeps_old_host_until_matching_ready_then_retains_the_union() {
        let (mut state, request, area) = growth_fixture();
        let host = state.actual_rect();
        let target = request.bubble;
        let plan = state.growth_plan(&request, 4, true, &[area]).unwrap();
        assert_eq!(Some(plan.rect_physical), target);
        state.prepare_carrier(request.clone(), plan);
        assert_eq!(state.actual_rect(), host);
        assert_eq!(state.applied.as_ref().unwrap().bubble, target);
        assert_eq!(state.last_revision, plan.layout_revision);
        assert!(state.carrier_valid_for(4));
        assert!(!state.applied_for(4));
        assert!(state.accepts_carrier(&plan, 4));
        assert!(!state.accepts(&request, 4));
        assert!(!state.refresh_actual(host, 1.25));
        assert!(state.accepts_carrier(&plan, 4));

        state.commit(request, Some(plan.rect_physical));
        state.retaining = true;
        state.refresh_actual(plan.rect_physical, 1.25);
        assert!(state.applied_for(4));
        assert!(state.pending_carrier.is_none());
        assert!(!state.accepts_carrier(&plan, 4));
        assert!(state.retaining);
        // The pet may recompute after the physical-size event; keep holding.
        let reflow = next_request(&state, target, true);
        let carrier = state.planned_carrier(&reflow, 4, true);
        state.commit(reflow, carrier);
        assert!(state.retaining);
        state.set_request(state.desired, false);
        let release = next_request(&state, target, false);
        let carrier = state.planned_carrier(&release, 4, true);
        state.commit(release, carrier);
        assert!(state.applied_for(4));
        assert!(!state.retaining);
    }
    #[test]
    fn growth_union_covers_all_directions_and_clamps_without_crossing_work_area() {
        let (state, request, area) = growth_fixture();
        let host = state.actual_rect();
        for target in [
            request.bubble.unwrap(),
            PhysicalRect {
                height: 600,
                ..host
            },
            PhysicalRect {
                x: -800,
                y: 200,
                width: 650,
                height: 600,
            },
            PhysicalRect {
                y: 200,
                width: 550,
                height: 600,
                ..host
            },
            PhysicalRect {
                y: 0,
                height: 600,
                ..host
            },
        ] {
            let request = AccessoryLayoutRequest {
                bubble: Some(target),
                ..request.clone()
            };
            let plan = state.growth_plan(&request, 4, true, &[area]).unwrap();
            assert!(plan.rect_physical.contains(host));
            assert!(plan.rect_physical.contains(target));
            assert!(area.contains(plan.rect_physical));
        }
        let adjacent = PhysicalRect { x: 0, ..area };
        let across = AccessoryLayoutRequest {
            bubble: Some(PhysicalRect { x: 100, ..host }),
            ..request.clone()
        };
        assert!(state
            .growth_plan(&across, 4, true, &[area, adjacent])
            .is_none());
        assert!(state.growth_plan(&request, 4, true, &[]).is_none());
        let taskbar = PhysicalRect {
            height: 650,
            ..area
        };
        assert!(state.growth_plan(&request, 4, true, &[taskbar]).is_none());
    }
    #[test]
    fn growth_ready_requires_every_plan_field_and_current_context() {
        let (mut state, request, area) = growth_fixture();
        let plan = state.growth_plan(&request, 4, true, &[area]).unwrap();
        state.prepare_carrier(request, plan);
        for stale in [
            BubbleCarrierPlan {
                owner: plan.owner + 1,
                ..plan
            },
            BubbleCarrierPlan {
                generation: plan.generation + 1,
                ..plan
            },
            BubbleCarrierPlan {
                layout_revision: plan.layout_revision + 1,
                ..plan
            },
            BubbleCarrierPlan {
                measurement_revision: plan.measurement_revision + 1,
                ..plan
            },
            BubbleCarrierPlan {
                rect_physical: PhysicalRect {
                    x: 1,
                    ..plan.rect_physical
                },
                ..plan
            },
        ] {
            assert!(!state.accepts_carrier(&stale, 4));
        }
        assert!(!state.accepts_carrier(&plan, 5));
        assert!(state.accepts_carrier(&plan, 4));
    }
    #[test]
    fn newer_measurement_or_layout_invalidates_a_prepared_growth() {
        for change_size in [false, true] {
            let (mut state, request, area) = growth_fixture();
            let plan = state.growth_plan(&request, 4, true, &[area]).unwrap();
            state.prepare_carrier(request, plan);
            state.set_request(
                if change_size {
                    (360, 420)
                } else {
                    state.desired
                },
                change_size,
            );
            assert!(state.pending_carrier.is_none());
            assert!(!state.accepts_carrier(&plan, 4));
            assert!(state.carrier_valid_for(4));
        }
        let (mut state, request, area) = growth_fixture();
        let old = state.growth_plan(&request, 4, true, &[area]).unwrap();
        state.prepare_carrier(request.clone(), old);
        let next = next_request(&state, request.bubble, true);
        let new = state.growth_plan(&next, 4, true, &[area]).unwrap();
        state.prepare_carrier(next, new);
        assert!(!state.accepts_carrier(&old, 4));
        assert!(state.accepts_carrier(&new, 4));
        let drag = next_request(&state, request.bubble, false);
        assert!(state.growth_plan(&drag, 4, true, &[area]).is_none());
        let carrier = state.planned_carrier(&drag, 4, true);
        assert_eq!(carrier, request.bubble);
        state.commit(drag, carrier);
        assert!(!state.accepts_carrier(&new, 4));
        assert!(state.pending_carrier.is_none());
        assert!(!state.retaining);
    }
    #[test]
    fn hidden_owner_displacement_or_dpr_changes_reject_growth_ready() {
        for scenario in 0..4 {
            let (mut state, request, area) = growth_fixture();
            let plan = state.growth_plan(&request, 4, true, &[area]).unwrap();
            state.prepare_carrier(request, plan);
            match scenario {
                0 => state.show_requested = false,
                1 => state.owner += 1,
                2 => {
                    state.refresh_actual(
                        PhysicalRect {
                            x: -590,
                            ..state.actual_rect()
                        },
                        1.25,
                    );
                }
                _ => {
                    state.refresh_actual(state.actual_rect(), 1.5);
                }
            }
            assert!(!state.accepts_carrier(&plan, 4));
        }
        let (state, request, area) = growth_fixture();
        assert!(state.growth_plan(&request, 4, false, &[area]).is_none());
        assert!(state.growth_plan(&request, 5, true, &[area]).is_none());
        let extreme = PhysicalRect {
            x: i32::MIN,
            y: 0,
            width: 1,
            height: 1,
        };
        assert!(extreme
            .union(PhysicalRect {
                x: i32::MAX,
                ..extreme
            })
            .is_none());
    }
    #[test]
    fn retained_native_rect_is_not_mistaken_for_displacement_but_real_moves_and_dpr_are() {
        let host = PhysicalRect {
            x: -500,
            y: 100,
            width: 450,
            height: 600,
        };
        let target = PhysicalRect {
            y: 300,
            height: 400,
            ..host
        };
        for (actual, scale) in [(PhysicalRect { x: -499, ..host }, 1.25), (host, 1.5)] {
            let mut state = committed_state(host, 1.25);
            state.set_request((360, 320), true);
            assert_eq!(state.measurement().desired_width_physical, 450);
            assert_eq!(state.measurement().desired_height_physical, 400);
            state.commit(next_request(&state, Some(target), true), Some(host));
            let revision = state.measurement_revision;
            assert!(!state.refresh_actual(host, 1.25));
            assert_eq!(state.measurement_revision, revision);
            assert!(state.applied_for(4));
            assert!(state.refresh_actual(actual, scale));
            assert_eq!(state.measurement_revision, revision + 1);
            assert!(state.carrier.is_none());
            assert!(state.applied.is_none());
            assert!(!state.retaining);
        }
    }
    #[test]
    fn queued_layout_has_no_visible_carrier_even_after_a_retained_frame() {
        let mut state = committed_state(request().bubble.unwrap(), 1.0);
        let request = next_request(&state, None, true);
        let carrier = state.planned_carrier(&request, 4, true);
        assert!(carrier.is_none());
        state.commit(request, carrier);
        assert!(!state.carrier_valid_for(4));
        assert!(!state.applied_for(4));
        assert!(!state.retaining);
    }
    #[test]
    fn constrained_size_does_not_replace_natural_content_measurement() {
        let state = LayoutState {
            desired: (360, 480),
            actual: (450, 180),
            scale: 1.25,
            ..Default::default()
        };
        let m = state.measurement();
        assert_eq!(m.desired_width_physical, 450);
        assert_eq!(m.desired_height_physical, 600);
        assert_eq!(m.height_physical, 180);
    }
    #[test]
    fn size_limits_and_dev_envelope_stay_compatible() {
        assert_eq!(clamp_bubble_size(100, 20), (240, 64));
        assert_eq!(clamp_bubble_size(900, 900), (360, 480));
        let env = build_test_envelope("test", "todo_reminder", 42);
        assert_eq!(env["seq"], 42);
        assert_eq!(env["source_kind"], "todo_reminder");
    }
}
