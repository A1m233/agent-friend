use super::PlatformPresence;
use std::os::raw::{c_double, c_uint};

const HID_EVENT_SOURCE: c_uint = 1;
const ANY_INPUT_EVENT: c_uint = u32::MAX;

#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGEventSourceSecondsSinceLastEventType(
        source_state_id: c_uint,
        event_type: c_uint,
    ) -> c_double;
}

pub(super) fn probe(window_focused: bool) -> PlatformPresence {
    let seconds =
        unsafe { CGEventSourceSecondsSinceLastEventType(HID_EVENT_SOURCE, ANY_INPUT_EVENT) };
    let idle_ms = if seconds.is_finite() && seconds >= 0.0 {
        Some((seconds * 1000.0) as u64)
    } else {
        None
    };
    PlatformPresence {
        idle_ms,
        // Losing focus immediately removes desktop eligibility. macOS lock/sleep observers can
        // later refine the diagnostic label without changing the fail-closed policy.
        session_state: if window_focused { "active" } else { "unknown" },
        capabilities: vec!["window", "idle"],
    }
}
