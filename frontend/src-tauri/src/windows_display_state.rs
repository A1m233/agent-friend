use std::ffi::c_void;

use tauri::{AppHandle, Manager};
use windows::Win32::{
    Foundation::{HANDLE, HWND, LPARAM, LRESULT, WIN32_ERROR, WPARAM},
    System::{
        Power::{
            PowerSettingRegisterNotification, PowerSettingUnregisterNotification,
            DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS, HPOWERNOTIFY, POWERBROADCAST_SETTING,
        },
        RemoteDesktop::{
            WTSFreeMemory, WTSQuerySessionInformationW, WTSRegisterSessionNotification,
            WTSSessionInfoEx, WTSUnRegisterSessionNotification, NOTIFY_FOR_THIS_SESSION,
            WTSINFOEXW, WTS_CURRENT_SESSION, WTS_SESSIONSTATE_LOCK, WTS_SESSIONSTATE_UNLOCK,
        },
        SystemServices::GUID_SESSION_DISPLAY_STATUS,
    },
    UI::{
        Shell::{DefSubclassProc, GetWindowSubclass, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::{
            DEVICE_NOTIFY_CALLBACK, WM_NCDESTROY, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK,
            WTS_SESSION_UNLOCK,
        },
    },
};

const ERROR_SUCCESS: WIN32_ERROR = WIN32_ERROR(0);

struct CallbackContext {
    app: AppHandle,
}

pub(crate) struct SessionDisplayObserver {
    registration: HPOWERNOTIFY,
    _context: Box<CallbackContext>,
}

impl SessionDisplayObserver {
    fn register(app: &AppHandle) -> Result<Self, String> {
        let mut context = Box::new(CallbackContext { app: app.clone() });
        let parameters = DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS {
            Callback: Some(display_status_callback),
            Context: (&mut *context as *mut CallbackContext).cast(),
        };
        let mut registration = std::ptr::null_mut();
        let result = unsafe {
            PowerSettingRegisterNotification(
                &GUID_SESSION_DISPLAY_STATUS,
                DEVICE_NOTIFY_CALLBACK,
                HANDLE(
                    (&parameters as *const DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS)
                        .cast_mut()
                        .cast(),
                ),
                &mut registration,
            )
        };
        if result != ERROR_SUCCESS {
            return Err(format!(
                "PowerSettingRegisterNotification failed with WIN32_ERROR {}",
                result.0
            ));
        }
        Ok(Self {
            registration: HPOWERNOTIFY(registration as isize),
            _context: context,
        })
    }
}

impl Drop for SessionDisplayObserver {
    fn drop(&mut self) {
        let result = unsafe { PowerSettingUnregisterNotification(self.registration) };
        if result != ERROR_SUCCESS {
            log::warn!(
                "windows session display observer: unregister failed with WIN32_ERROR {}",
                result.0
            );
        }
    }
}

pub(crate) fn install(app: &AppHandle) {
    // Setup runs on the HWND's owning thread. Subscribe before registering the
    // power callback so an initial On cannot race ahead of the lock snapshot.
    if let Err(error) = install_session_observer(app) {
        log::warn!("windows session lock observer: {error}");
    }
    match SessionDisplayObserver::register(app) {
        Ok(observer) => {
            if app.manage(observer) {
                log::info!("windows session display observer registered");
            } else {
                log::warn!("windows session display observer was already registered");
            }
        }
        Err(error) => log::warn!("windows session display observer: {error}"),
    }
}

unsafe extern "system" fn display_status_callback(
    context: *const c_void,
    _event_type: u32,
    setting: *const c_void,
) -> u32 {
    if context.is_null() || setting.is_null() {
        return ERROR_SUCCESS.0;
    }
    let context = unsafe { &*(context.cast::<CallbackContext>()) };
    let setting = unsafe { &*(setting.cast::<POWERBROADCAST_SETTING>()) };
    if setting.PowerSetting != GUID_SESSION_DISPLAY_STATUS || setting.DataLength < 4 {
        return ERROR_SUCCESS.0;
    }
    let raw_state = unsafe { std::ptr::read_unaligned(setting.Data.as_ptr().cast::<u32>()) };
    let app = context.app.clone();
    let handle = app.clone();
    // Serialize power and WTS observations on the window thread. In particular,
    // a queued power notification must not restore a pet after a newer lock.
    if let Err(error) = app.run_on_main_thread(move || {
        if raw_state == 1 {
            match current_session_locked() {
                Ok(locked) => crate::pet_display_recovery::handle_session_locked(
                    &handle,
                    locked,
                    "display-on-query",
                ),
                Err(error) => log::warn!("windows display-on lock query failed: {error}"),
            }
        }
        crate::pet_display_recovery::handle_session_display_state(&handle, raw_state);
    }) {
        log::warn!("windows session display observation dispatch failed: {error}");
    }
    ERROR_SUCCESS.0
}

const SESSION_SUBCLASS_ID: usize = 0xAF_5345;

fn install_session_observer(app: &AppHandle) -> Result<(), String> {
    let pet = app
        .get_webview_window("pet")
        .ok_or("pet window is unavailable")?;
    let hwnd = HWND(pet.hwnd().map_err(|error| error.to_string())?.0);
    let mut existing_context = 0;
    if unsafe {
        GetWindowSubclass(
            hwnd,
            Some(session_subclass),
            SESSION_SUBCLASS_ID,
            Some(&mut existing_context),
        )
        .as_bool()
    } {
        return Ok(());
    }
    unsafe { WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION) }
        .map_err(|error| format!("WTSRegisterSessionNotification failed: {error}"))?;
    let context = Box::into_raw(Box::new(app.clone()));
    unsafe {
        if !SetWindowSubclass(
            hwnd,
            Some(session_subclass),
            SESSION_SUBCLASS_ID,
            context as usize,
        )
        .as_bool()
        {
            let _ = WTSUnRegisterSessionNotification(hwnd);
            drop(Box::from_raw(context));
            return Err("SetWindowSubclass failed".into());
        }
    }
    match current_session_locked() {
        Ok(locked) => {
            crate::pet_display_recovery::handle_session_locked(app, locked, "initial-query");
        }
        Err(error) => log::warn!("windows initial session lock query failed: {error}"),
    }
    log::info!("windows session lock observer registered");
    Ok(())
}

// The subclass owns its AppHandle until WM_NCDESTROY, including when the pet is
// hidden. All installation, callbacks and cleanup run on the owning UI thread.
unsafe extern "system" fn session_subclass(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    subclass_id: usize,
    context: usize,
) -> LRESULT {
    if message == WM_WTSSESSION_CHANGE {
        let locked = match wparam.0 as u32 {
            WTS_SESSION_LOCK => Some(true),
            WTS_SESSION_UNLOCK => Some(false),
            _ => None,
        };
        if let Some(locked) = locked {
            let app = unsafe { &*(context as *const AppHandle) }.clone();
            crate::pet_display_recovery::handle_session_locked(&app, locked, "wts-notification");
        }
    } else if message == WM_NCDESTROY {
        unsafe {
            let _ = WTSUnRegisterSessionNotification(hwnd);
            let _ = RemoveWindowSubclass(hwnd, Some(session_subclass), subclass_id);
            drop(Box::from_raw(context as *mut AppHandle));
        }
    }
    unsafe { DefSubclassProc(hwnd, message, wparam, lparam) }
}

fn current_session_locked() -> Result<bool, String> {
    let mut buffer = windows::core::PWSTR::null();
    let mut bytes = 0;
    unsafe {
        WTSQuerySessionInformationW(
            None,
            WTS_CURRENT_SESSION,
            WTSSessionInfoEx,
            &mut buffer,
            &mut bytes,
        )
        .map_err(|error| format!("WTSQuerySessionInformationW failed: {error}"))?;
        let result = if !buffer.is_null() && bytes as usize >= std::mem::size_of::<WTSINFOEXW>() {
            let info = &*buffer.0.cast::<WTSINFOEXW>();
            if info.Level == 1 {
                match info.Data.WTSInfoExLevel1.SessionFlags as u32 {
                    WTS_SESSIONSTATE_LOCK => Ok(true),
                    WTS_SESSIONSTATE_UNLOCK => Ok(false),
                    _ => Err("session lock state is unknown".into()),
                }
            } else {
                Err("unsupported session information level".into())
            }
        } else {
            Err("session information is missing or truncated".into())
        };
        WTSFreeMemory(buffer.0.cast());
        result
    }
}
