use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{Manager, WebviewWindow, Wry};
use tauri_plugin_opener::OpenerExt;
use tokio::sync::oneshot;
use webview2_com::{
    take_pwstr, GetNonDefaultPermissionSettingsCompletedHandler,
    Microsoft::Web::WebView2::Win32::{
        ICoreWebView2PermissionSettingCollectionView, ICoreWebView2Profile4, ICoreWebView2_13,
        COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
        COREWEBVIEW2_PERMISSION_STATE, COREWEBVIEW2_PERMISSION_STATE_ALLOW,
        COREWEBVIEW2_PERMISSION_STATE_DEFAULT, COREWEBVIEW2_PERMISSION_STATE_DENY,
    },
    SetPermissionStateCompletedHandler,
};
use windows::{
    core::{Interface, HSTRING, PWSTR},
    Devices::Enumeration::{DeviceAccessInformation, DeviceAccessStatus, DeviceClass},
    Security::Authorization::AppCapabilityAccess::{AppCapability, AppCapabilityAccessStatus},
};

use crate::permissions::{
    model::{
        PermissionId, PermissionRecoveryAction, SystemPermissionObservation, SystemPermissionState,
    },
    platform::{
        PermissionPlatformAdapter, PermissionPlatformError, PermissionPlatformFuture,
        RequestInstruction,
    },
};

pub(crate) struct WindowsPermissionAdapter {
    app: tauri::AppHandle,
}

impl WindowsPermissionAdapter {
    pub(crate) fn new(app: tauri::AppHandle) -> Self {
        Self { app }
    }

    fn microphone_window(&self) -> Result<WebviewWindow<Wry>, PermissionPlatformError> {
        self.app
            .get_webview_window("settings")
            .or_else(|| self.app.webview_windows().into_values().next())
            .ok_or_else(|| PermissionPlatformError {
                code: "permission_webview_missing".to_string(),
                message: "no WebView is available for microphone profile inspection".to_string(),
            })
    }
}

impl PermissionPlatformAdapter for WindowsPermissionAdapter {
    fn query_permission(
        &self,
        id: PermissionId,
    ) -> PermissionPlatformFuture<'_, SystemPermissionObservation> {
        Box::pin(async move {
            if id != PermissionId::Microphone {
                return Ok(SystemPermissionObservation {
                    state: SystemPermissionState::Unsupported,
                    recovery_action: PermissionRecoveryAction::None,
                });
            }
            let window = self.microphone_window()?;
            let url = window.url().map_err(|error| PermissionPlatformError {
                code: "permission_webview_url_failed".to_string(),
                message: error.to_string(),
            })?;
            let origin = url.origin().ascii_serialization();
            let profile = probe_webview_profile(&window).await.map_err(|message| {
                PermissionPlatformError {
                    code: "permission_webview_profile_query_failed".to_string(),
                    message,
                }
            })?;
            let profile_state = profile
                .microphone_settings
                .iter()
                .find(|setting| {
                    setting.origin.trim_end_matches('/') == origin.trim_end_matches('/')
                })
                .map(|setting| setting.state.clone())
                .unwrap_or(WebViewPermissionState::Default);
            Ok(combine_microphone_observation(
                &probe_microphone_os_access(),
                profile_state,
            ))
        })
    }

    fn prepare_request(
        &self,
        id: PermissionId,
    ) -> PermissionPlatformFuture<'_, RequestInstruction> {
        Box::pin(async move {
            let observation = self.query_permission(id).await?;
            match observation.recovery_action {
                PermissionRecoveryAction::Request => Ok(RequestInstruction::WebMedia),
                PermissionRecoveryAction::RetryRequest => {
                    let window = self.microphone_window()?;
                    let origin = window
                        .url()
                        .map_err(|error| PermissionPlatformError {
                            code: "permission_webview_url_failed".to_string(),
                            message: error.to_string(),
                        })?
                        .origin()
                        .ascii_serialization();
                    set_microphone_profile_state(&window, &origin, WebViewPermissionState::Default)
                        .await
                        .map_err(|message| PermissionPlatformError {
                            code: "permission_webview_retry_reset_failed".to_string(),
                            message,
                        })?;
                    Ok(RequestInstruction::WebMedia)
                }
                PermissionRecoveryAction::OpenSystemSettings => {
                    Ok(RequestInstruction::OpenSettings)
                }
                PermissionRecoveryAction::None => Ok(RequestInstruction::NotAvailable),
            }
        })
    }

    fn open_system_settings(&self, id: PermissionId) -> PermissionPlatformFuture<'_, ()> {
        Box::pin(async move {
            match id {
                PermissionId::Microphone => self
                    .app
                    .opener()
                    .open_url("ms-settings:privacy-microphone", None::<&str>)
                    .map_err(|error| PermissionPlatformError {
                        code: "permission_open_settings_failed".to_string(),
                        message: error.to_string(),
                    }),
            }
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum WindowsMicrophoneAccessStatus {
    Allowed,
    DeniedByUser,
    DeniedBySystem,
    UserPromptRequired,
    NotDeclaredByApp,
    Unspecified,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum WebViewPermissionState {
    Default,
    Allow,
    Deny,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WindowsMicrophoneOsProbe {
    pub device_access: Result<WindowsMicrophoneAccessStatus, String>,
    pub app_capability: Result<WindowsMicrophoneAccessStatus, String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WebViewMicrophonePermissionSetting {
    pub origin: String,
    pub state: WebViewPermissionState,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WindowsWebViewProfileProbe {
    pub profile_name: String,
    pub profile_path: String,
    pub microphone_settings: Vec<WebViewMicrophonePermissionSetting>,
}

#[cfg(debug_assertions)]
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WebViewPermissionRoundTripProbe {
    pub origin: String,
    pub denied: WindowsWebViewProfileProbe,
    pub restored: WindowsWebViewProfileProbe,
}

pub(crate) fn probe_microphone_os_access() -> WindowsMicrophoneOsProbe {
    let device_access = DeviceAccessInformation::CreateFromDeviceClass(DeviceClass::AudioCapture)
        .and_then(|access| access.CurrentStatus())
        .map(map_device_access_status)
        .map_err(|error| error.to_string());

    let app_capability = AppCapability::Create(&HSTRING::from("microphone"))
        .and_then(|capability| capability.CheckAccess())
        .map(map_app_capability_status)
        .map_err(|error| error.to_string());

    WindowsMicrophoneOsProbe {
        device_access,
        app_capability,
    }
}

pub(crate) async fn probe_webview_profile(
    window: &WebviewWindow<Wry>,
) -> Result<WindowsWebViewProfileProbe, String> {
    type ProbeResult = Result<WindowsWebViewProfileProbe, String>;

    let (sender, receiver) = oneshot::channel::<ProbeResult>();
    let sender = Arc::new(Mutex::new(Some(sender)));
    let closure_sender = sender.clone();

    window
        .with_webview(move |webview| {
            let setup_result = (|| -> webview2_com::Result<()> {
                let core = unsafe { webview.controller().CoreWebView2()? };
                let profile = unsafe { core.cast::<ICoreWebView2_13>()?.Profile()? };
                let profile4 = profile.cast::<ICoreWebView2Profile4>()?;
                let profile_name =
                    read_profile_string(|value| unsafe { profile4.ProfileName(value) })?;
                let profile_path =
                    read_profile_string(|value| unsafe { profile4.ProfilePath(value) })?;

                let callback_sender = closure_sender.clone();
                let handler = GetNonDefaultPermissionSettingsCompletedHandler::create(Box::new(
                    move |error_code, collection| {
                        let result = (|| -> webview2_com::Result<WindowsWebViewProfileProbe> {
                            error_code?;
                            let collection = collection.ok_or_else(|| {
                                webview2_com::Error::CallbackError(
                                    "permission settings collection missing".to_string(),
                                )
                            })?;
                            Ok(WindowsWebViewProfileProbe {
                                profile_name: profile_name.clone(),
                                profile_path: profile_path.clone(),
                                microphone_settings: read_microphone_settings(&collection)?,
                            })
                        })()
                        .map_err(|error| error.to_string());
                        complete_probe(&callback_sender, result);
                        Ok(())
                    },
                ));

                unsafe { profile4.GetNonDefaultPermissionSettings(&handler)? };
                Ok(())
            })();

            if let Err(error) = setup_result {
                complete_probe(&closure_sender, Err(error.to_string()));
            }
        })
        .map_err(|error| error.to_string())?;

    receiver
        .await
        .map_err(|_| "WebView2 permission probe callback was canceled".to_string())?
}

#[cfg(debug_assertions)]
pub(crate) async fn probe_microphone_permission_round_trip(
    window: &WebviewWindow<Wry>,
    origin: &str,
) -> Result<WebViewPermissionRoundTripProbe, String> {
    set_microphone_profile_state(window, origin, WebViewPermissionState::Deny).await?;
    let denied = probe_webview_profile(window).await;

    // Always attempt cleanup, even if the intermediate read failed. The synthetic
    // origin must not leave a non-default permission entry in the user's profile.
    let restore =
        set_microphone_profile_state(window, origin, WebViewPermissionState::Default).await;
    if let Err(error) = restore {
        return Err(format!(
            "failed to restore synthetic origin to default: {error}"
        ));
    }

    Ok(WebViewPermissionRoundTripProbe {
        origin: origin.to_string(),
        denied: denied?,
        restored: probe_webview_profile(window).await?,
    })
}

pub(crate) async fn set_microphone_profile_state(
    window: &WebviewWindow<Wry>,
    origin: &str,
    state: WebViewPermissionState,
) -> Result<(), String> {
    let state = match state {
        WebViewPermissionState::Default => COREWEBVIEW2_PERMISSION_STATE_DEFAULT,
        WebViewPermissionState::Allow => COREWEBVIEW2_PERMISSION_STATE_ALLOW,
        WebViewPermissionState::Deny => COREWEBVIEW2_PERMISSION_STATE_DENY,
        WebViewPermissionState::Unknown => {
            return Err("cannot write an unknown WebView permission state".to_string())
        }
    };
    let origin = HSTRING::from(origin);
    let (sender, receiver) = oneshot::channel::<Result<(), String>>();
    let sender = Arc::new(Mutex::new(Some(sender)));
    let closure_sender = sender.clone();

    window
        .with_webview(move |webview| {
            let setup_result = (|| -> webview2_com::Result<()> {
                let core = unsafe { webview.controller().CoreWebView2()? };
                let profile = unsafe { core.cast::<ICoreWebView2_13>()?.Profile()? };
                let profile4 = profile.cast::<ICoreWebView2Profile4>()?;
                let callback_sender = closure_sender.clone();
                let handler =
                    SetPermissionStateCompletedHandler::create(Box::new(move |error_code| {
                        complete_unit_probe(
                            &callback_sender,
                            error_code.map_err(|error| error.to_string()),
                        );
                        Ok(())
                    }));
                unsafe {
                    profile4.SetPermissionState(
                        COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
                        &origin,
                        state,
                        &handler,
                    )?;
                }
                Ok(())
            })();

            if let Err(error) = setup_result {
                complete_unit_probe(&closure_sender, Err(error.to_string()));
            }
        })
        .map_err(|error| error.to_string())?;

    receiver
        .await
        .map_err(|_| "WebView2 permission state callback was canceled".to_string())?
}

fn complete_probe(
    sender: &Arc<Mutex<Option<oneshot::Sender<Result<WindowsWebViewProfileProbe, String>>>>>,
    result: Result<WindowsWebViewProfileProbe, String>,
) {
    if let Ok(mut slot) = sender.lock() {
        if let Some(sender) = slot.take() {
            let _ = sender.send(result);
        }
    }
}

fn complete_unit_probe(
    sender: &Arc<Mutex<Option<oneshot::Sender<Result<(), String>>>>>,
    result: Result<(), String>,
) {
    if let Ok(mut slot) = sender.lock() {
        if let Some(sender) = slot.take() {
            let _ = sender.send(result);
        }
    }
}

fn read_profile_string(
    reader: impl FnOnce(*mut PWSTR) -> windows::core::Result<()>,
) -> webview2_com::Result<String> {
    let mut value = PWSTR::null();
    reader(&mut value)?;
    Ok(take_pwstr(value))
}

fn read_microphone_settings(
    collection: &ICoreWebView2PermissionSettingCollectionView,
) -> webview2_com::Result<Vec<WebViewMicrophonePermissionSetting>> {
    let mut count = 0;
    unsafe { collection.Count(&mut count)? };
    let mut settings = Vec::new();

    for index in 0..count {
        let setting = unsafe { collection.GetValueAtIndex(index)? };
        let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
        unsafe { setting.PermissionKind(&mut kind)? };
        if kind != COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
            continue;
        }

        let mut origin = PWSTR::null();
        let mut state = COREWEBVIEW2_PERMISSION_STATE::default();
        unsafe {
            setting.PermissionOrigin(&mut origin)?;
            setting.PermissionState(&mut state)?;
        }
        settings.push(WebViewMicrophonePermissionSetting {
            origin: take_pwstr(origin),
            state: map_webview_permission_state(state),
        });
    }

    Ok(settings)
}

fn map_device_access_status(status: DeviceAccessStatus) -> WindowsMicrophoneAccessStatus {
    if status == DeviceAccessStatus::Allowed {
        WindowsMicrophoneAccessStatus::Allowed
    } else if status == DeviceAccessStatus::DeniedByUser {
        WindowsMicrophoneAccessStatus::DeniedByUser
    } else if status == DeviceAccessStatus::DeniedBySystem {
        WindowsMicrophoneAccessStatus::DeniedBySystem
    } else if status == DeviceAccessStatus::Unspecified {
        WindowsMicrophoneAccessStatus::Unspecified
    } else {
        WindowsMicrophoneAccessStatus::Unknown
    }
}

fn map_app_capability_status(status: AppCapabilityAccessStatus) -> WindowsMicrophoneAccessStatus {
    if status == AppCapabilityAccessStatus::Allowed {
        WindowsMicrophoneAccessStatus::Allowed
    } else if status == AppCapabilityAccessStatus::DeniedByUser {
        WindowsMicrophoneAccessStatus::DeniedByUser
    } else if status == AppCapabilityAccessStatus::DeniedBySystem {
        WindowsMicrophoneAccessStatus::DeniedBySystem
    } else if status == AppCapabilityAccessStatus::UserPromptRequired {
        WindowsMicrophoneAccessStatus::UserPromptRequired
    } else if status == AppCapabilityAccessStatus::NotDeclaredByApp {
        WindowsMicrophoneAccessStatus::NotDeclaredByApp
    } else {
        WindowsMicrophoneAccessStatus::Unknown
    }
}

fn map_webview_permission_state(state: COREWEBVIEW2_PERMISSION_STATE) -> WebViewPermissionState {
    if state == COREWEBVIEW2_PERMISSION_STATE_DEFAULT {
        WebViewPermissionState::Default
    } else if state == COREWEBVIEW2_PERMISSION_STATE_ALLOW {
        WebViewPermissionState::Allow
    } else if state == COREWEBVIEW2_PERMISSION_STATE_DENY {
        WebViewPermissionState::Deny
    } else {
        WebViewPermissionState::Unknown
    }
}

fn combine_microphone_observation(
    os_probe: &WindowsMicrophoneOsProbe,
    profile_state: WebViewPermissionState,
) -> SystemPermissionObservation {
    let os_states = [
        os_probe.device_access.as_ref().ok(),
        os_probe.app_capability.as_ref().ok(),
    ];
    let has = |expected| os_states.iter().flatten().any(|state| **state == expected);

    if has(WindowsMicrophoneAccessStatus::DeniedBySystem) {
        return SystemPermissionObservation {
            state: SystemPermissionState::Restricted,
            recovery_action: PermissionRecoveryAction::OpenSystemSettings,
        };
    }
    if has(WindowsMicrophoneAccessStatus::DeniedByUser) {
        return SystemPermissionObservation {
            state: SystemPermissionState::Denied,
            recovery_action: PermissionRecoveryAction::OpenSystemSettings,
        };
    }
    if profile_state == WebViewPermissionState::Deny {
        return SystemPermissionObservation {
            state: SystemPermissionState::Denied,
            recovery_action: PermissionRecoveryAction::RetryRequest,
        };
    }
    if has(WindowsMicrophoneAccessStatus::Allowed) {
        return match profile_state {
            WebViewPermissionState::Allow => SystemPermissionObservation {
                state: SystemPermissionState::Granted,
                recovery_action: PermissionRecoveryAction::None,
            },
            WebViewPermissionState::Default => SystemPermissionObservation {
                state: SystemPermissionState::NotDetermined,
                recovery_action: PermissionRecoveryAction::Request,
            },
            WebViewPermissionState::Deny | WebViewPermissionState::Unknown => unreachable!(),
        };
    }
    if has(WindowsMicrophoneAccessStatus::NotDeclaredByApp) {
        return SystemPermissionObservation {
            state: SystemPermissionState::Unsupported,
            recovery_action: PermissionRecoveryAction::None,
        };
    }
    SystemPermissionObservation {
        state: SystemPermissionState::Unknown,
        recovery_action: PermissionRecoveryAction::None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_windows_access_statuses_without_collapsing_denial_sources() {
        assert_eq!(
            map_device_access_status(DeviceAccessStatus::DeniedByUser),
            WindowsMicrophoneAccessStatus::DeniedByUser
        );
        assert_eq!(
            map_device_access_status(DeviceAccessStatus::DeniedBySystem),
            WindowsMicrophoneAccessStatus::DeniedBySystem
        );
        assert_eq!(
            map_app_capability_status(AppCapabilityAccessStatus::UserPromptRequired),
            WindowsMicrophoneAccessStatus::UserPromptRequired
        );
    }

    #[test]
    fn maps_webview_profile_states() {
        assert_eq!(
            map_webview_permission_state(COREWEBVIEW2_PERMISSION_STATE_DEFAULT),
            WebViewPermissionState::Default
        );
        assert_eq!(
            map_webview_permission_state(COREWEBVIEW2_PERMISSION_STATE_ALLOW),
            WebViewPermissionState::Allow
        );
        assert_eq!(
            map_webview_permission_state(COREWEBVIEW2_PERMISSION_STATE_DENY),
            WebViewPermissionState::Deny
        );
    }

    #[test]
    fn passive_os_probe_is_callable_for_the_current_process() {
        let probe = probe_microphone_os_access();
        assert!(
            probe.device_access.is_ok() || probe.app_capability.is_ok(),
            "at least one supported Windows API should return a status: {probe:?}"
        );
    }

    #[test]
    fn combines_os_and_profile_without_optimistic_grants() {
        let allowed = WindowsMicrophoneOsProbe {
            device_access: Ok(WindowsMicrophoneAccessStatus::Allowed),
            app_capability: Ok(WindowsMicrophoneAccessStatus::Allowed),
        };
        assert_eq!(
            combine_microphone_observation(&allowed, WebViewPermissionState::Allow).state,
            SystemPermissionState::Granted
        );
        assert_eq!(
            combine_microphone_observation(&allowed, WebViewPermissionState::Default).state,
            SystemPermissionState::NotDetermined
        );
        assert_eq!(
            combine_microphone_observation(&allowed, WebViewPermissionState::Deny).recovery_action,
            PermissionRecoveryAction::RetryRequest
        );

        let restricted = WindowsMicrophoneOsProbe {
            device_access: Ok(WindowsMicrophoneAccessStatus::DeniedBySystem),
            app_capability: Ok(WindowsMicrophoneAccessStatus::Allowed),
        };
        assert_eq!(
            combine_microphone_observation(&restricted, WebViewPermissionState::Allow).state,
            SystemPermissionState::Restricted
        );
    }
}
