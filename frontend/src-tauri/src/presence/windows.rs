use super::PlatformPresence;
use std::mem::size_of;
use windows::Win32::System::StationsAndDesktops::{
    CloseDesktop, OpenInputDesktop, SwitchDesktop, DESKTOP_CONTROL_FLAGS, DESKTOP_SWITCHDESKTOP,
};
use windows::Win32::System::SystemInformation::GetTickCount;
use windows::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};

pub(super) fn probe() -> PlatformPresence {
    let idle_ms = unsafe {
        let mut info = LASTINPUTINFO {
            cbSize: size_of::<LASTINPUTINFO>() as u32,
            dwTime: 0,
        };
        if GetLastInputInfo(&mut info).as_bool() {
            Some(GetTickCount().wrapping_sub(info.dwTime) as u64)
        } else {
            None
        }
    };
    let session_state = if desktop_is_switchable() {
        "active"
    } else {
        "locked"
    };
    PlatformPresence {
        idle_ms,
        session_state,
        capabilities: vec!["window", "idle", "lock"],
    }
}

fn desktop_is_switchable() -> bool {
    unsafe {
        let Ok(desktop) = OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_SWITCHDESKTOP)
        else {
            return false;
        };
        let switchable = SwitchDesktop(desktop).is_ok();
        let _ = CloseDesktop(desktop);
        switchable
    }
}
