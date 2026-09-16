use super::{dispatching, quote_arguments, LaunchRequest};
use windows::{
    core::{Interface, BSTR},
    Win32::{
        Foundation::{CloseHandle, HANDLE},
        Security::{EqualSid, GetTokenInformation, TokenUser, TOKEN_QUERY, TOKEN_USER},
        System::{
            Com::{
                CoCreateInstance, CoInitializeEx, CoUninitialize, IDispatch, IServiceProvider,
                CLSCTX_LOCAL_SERVER, COINIT_APARTMENTTHREADED,
            },
            RemoteDesktop::ProcessIdToSessionId,
            Threading::{
                GetCurrentProcess, GetCurrentProcessId, OpenProcess, OpenProcessToken,
                PROCESS_QUERY_LIMITED_INFORMATION,
            },
            Variant::VARIANT,
        },
        UI::{
            Shell::{
                IShellBrowser, IShellDispatch2, IShellFolderViewDual, IShellWindows,
                SID_STopLevelBrowser, ShellWindows, SVGIO_BACKGROUND, SWC_DESKTOP,
                SWFO_NEEDDISPATCH,
            },
            WindowsAndMessaging::{GetShellWindow, GetWindowThreadProcessId},
        },
    },
};

struct OwnedHandle(HANDLE);
impl Drop for OwnedHandle {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}
struct ComApartment;
impl Drop for ComApartment {
    fn drop(&mut self) {
        unsafe {
            CoUninitialize();
        }
    }
}

unsafe fn user_token(process: HANDLE) -> windows::core::Result<Vec<u64>> {
    let mut token = HANDLE::default();
    unsafe {
        OpenProcessToken(process, TOKEN_QUERY, &mut token)?;
    }
    let token = OwnedHandle(token);
    let mut size = 0;
    unsafe {
        let _ = GetTokenInformation(token.0, TokenUser, None, 0, &mut size);
    }
    if size == 0 {
        return Err(windows::core::Error::from_win32());
    }
    let mut data = vec![0u64; (size as usize).div_ceil(8)];
    unsafe {
        GetTokenInformation(
            token.0,
            TokenUser,
            Some(data.as_mut_ptr().cast()),
            size,
            &mut size,
        )?;
    }
    Ok(data)
}

fn same_desktop_user() -> windows::core::Result<()> {
    unsafe {
        let window = GetShellWindow();
        if window.is_invalid() {
            return Err(windows::core::Error::from_win32());
        }
        let mut pid = 0;
        GetWindowThreadProcessId(window, Some(&mut pid));
        let mut own_session = 0;
        let mut shell_session = 0;
        ProcessIdToSessionId(GetCurrentProcessId(), &mut own_session)?;
        ProcessIdToSessionId(pid, &mut shell_session)?;
        if own_session == 0 || own_session != shell_session {
            return Err(windows::core::Error::from_win32());
        }
        let process = OwnedHandle(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid)?);
        let own = user_token(GetCurrentProcess())?;
        let shell = user_token(process.0)?;
        let own = &*own.as_ptr().cast::<TOKEN_USER>();
        let shell = &*shell.as_ptr().cast::<TOKEN_USER>();
        EqualSid(own.User.Sid, shell.User.Sid)?;
        Ok(())
    }
}

pub(super) fn launch(request: &LaunchRequest) -> Result<(), (&'static str, Option<i32>)> {
    same_desktop_user().map_err(|e| ("desktop_identity_unavailable", Some(e.code().0)))?;
    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            .ok()
            .map_err(|e| ("desktop_unavailable", Some(e.code().0)))?;
    }
    let _apartment = ComApartment;
    let shell =
        unsafe { desktop_shell() }.map_err(|e| ("desktop_unavailable", Some(e.code().0)))?;
    // Last validation before the irreversible COM request. No fallback launch path.
    super::validate(request).map_err(|code| (code, None))?;
    dispatching()?;
    unsafe {
        shell.ShellExecute(
            &BSTR::from(&request.executable_path),
            &VARIANT::from(quote_arguments(&request.arguments).as_str()),
            &VARIANT::from(request.working_directory.as_str()),
            &VARIANT::from("open"),
            &VARIANT::from(1i32),
        )
    }
    .map_err(|e| ("shell_dispatch_failed", Some(e.code().0)))
}

unsafe fn desktop_shell() -> windows::core::Result<IShellDispatch2> {
    unsafe {
        let windows: IShellWindows = CoCreateInstance(&ShellWindows, None, CLSCTX_LOCAL_SERVER)?;
        let empty = VARIANT::default();
        let mut hwnd = 0;
        let dispatch =
            windows.FindWindowSW(&empty, &empty, SWC_DESKTOP, &mut hwnd, SWFO_NEEDDISPATCH)?;
        let provider: IServiceProvider = dispatch.cast()?;
        let browser: IShellBrowser = provider.QueryService(&SID_STopLevelBrowser)?;
        let view = browser.QueryActiveShellView()?;
        let dispatch: IDispatch = view.GetItemObject(SVGIO_BACKGROUND)?;
        let folder: IShellFolderViewDual = dispatch.cast()?;
        folder.Application()?.cast()
    }
}
