use std::ffi::c_void;

use tauri::{AppHandle, Manager};
use windows::Win32::{
    Foundation::{HANDLE, WIN32_ERROR},
    System::{
        Power::{
            PowerSettingRegisterNotification, PowerSettingUnregisterNotification,
            DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS, HPOWERNOTIFY, POWERBROADCAST_SETTING,
        },
        SystemServices::GUID_SESSION_DISPLAY_STATUS,
    },
    UI::WindowsAndMessaging::DEVICE_NOTIFY_CALLBACK,
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
    crate::pet_display_recovery::handle_session_display_state(&context.app, raw_state);
    ERROR_SUCCESS.0
}
