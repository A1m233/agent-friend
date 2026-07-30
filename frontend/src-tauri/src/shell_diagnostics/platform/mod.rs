#[cfg(not(target_os = "windows"))]
use super::model::{RawShellEvent, ShellSnapshot};
#[cfg(not(target_os = "windows"))]
use std::{
    collections::BTreeMap,
    sync::{atomic::AtomicU64, mpsc::SyncSender, Arc},
};

#[cfg(target_os = "windows")]
mod windows;

#[cfg(target_os = "windows")]
pub(crate) use windows::{capture_snapshot, start_observer, ObserverHandle};

#[cfg(target_os = "windows")]
pub(crate) const SUPPORTED: bool = true;

#[cfg(not(target_os = "windows"))]
pub(crate) const SUPPORTED: bool = false;

#[cfg(not(target_os = "windows"))]
pub(crate) struct ObserverHandle;

#[cfg(not(target_os = "windows"))]
impl ObserverHandle {
    pub(crate) fn stop(self) {}
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn start_observer(
    _event_tx: SyncSender<RawShellEvent>,
    _dropped_events: Arc<AtomicU64>,
) -> Result<ObserverHandle, String> {
    Ok(ObserverHandle)
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn capture_snapshot(_app_windows: &BTreeMap<usize, String>) -> ShellSnapshot {
    ShellSnapshot::unsupported()
}
