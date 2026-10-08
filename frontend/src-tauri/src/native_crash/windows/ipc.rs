use super::super::store;
use std::{
    cell::UnsafeCell,
    ffi::{c_void, OsString},
    fs::File,
    mem::size_of,
    os::windows::{ffi::OsStrExt, io::AsRawHandle},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU32, Ordering},
};
use windows::{
    core::{PCWSTR, PWSTR},
    Win32::{
        Foundation::*,
        Security::SECURITY_ATTRIBUTES,
        Storage::FileSystem::{
            GetFinalPathNameByHandleW, MoveFileExW, FILE_NAME_NORMALIZED,
            MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        },
        System::{
            Diagnostics::{
                Debug::{CONTEXT, EXCEPTION_RECORD},
                ToolHelp::*,
            },
            Memory::*,
            Threading::*,
        },
    },
};

pub(super) const MAGIC: u64 = 0x41464e4352415348;
pub(super) const SERVICE_SLOTS: usize = 32;
pub(super) const META_CAP: usize = 16384;
pub(super) const STATE_IDLE: u32 = 0;
pub(super) const STATE_WRITING: u32 = 1;
pub(super) const STATE_REQUEST: u32 = 2;
pub(super) const STATE_WORKING: u32 = 3;
pub(super) const STATE_DONE: u32 = 4;

pub(super) const HANDOFF_TIMEOUT: u32 = 1;
pub(super) const HANDOFF_WAIT_FAILED: u32 = 2;
pub(super) const HANDOFF_WATCHER_EXITED: u32 = 3;
pub(super) const HANDOFF_PROTOCOL_FAILED: u32 = 4;
pub(super) const HANDOFF_NOTIFY_FAILED: u32 = 5;

pub(super) fn handoff_failure_code(value: u32) -> Option<&'static str> {
    match value {
        0 => None,
        HANDOFF_TIMEOUT => Some("handoff-timeout"),
        HANDOFF_WAIT_FAILED => Some("handoff-wait-failed"),
        HANDOFF_WATCHER_EXITED => Some("handoff-watcher-exited"),
        HANDOFF_NOTIFY_FAILED => Some("handoff-notify-failed"),
        _ => Some("handoff-protocol-failed"),
    }
}

#[repr(C)]
pub(super) struct Payload {
    pub kind: u32,
    pub thread_id: u32,
    pub sequence: u32,
    pub record: EXCEPTION_RECORD,
    pub context: AlignedContext,
}

/// windows-rs models CONTEXT with alignment 8 on x64; RtlCaptureContext requires 16.
#[repr(C, align(16))]
pub(super) struct AlignedContext(pub CONTEXT);
#[repr(C)]
#[derive(Clone, Copy)]
pub(super) struct ServicePayload {
    pub handle: usize,
    pub role: u32,
    pub generation: u64,
    pub pid: u32,
    pub created: u64,
}
#[repr(C)]
pub(super) struct ServiceSlot {
    pub state: AtomicU32,
    pub value: UnsafeCell<ServicePayload>,
}

#[repr(C)]
pub(super) struct Shared {
    pub magic: u64,
    pub version: u32,
    pub structure_bytes: u32,
    pub fixture_flags: u32,
    pub target_pid: u32,
    pub target_created: u64,
    pub metadata_len: u32,
    pub metadata: [u8; META_CAP],
    pub root_len: u32,
    pub root: [u16; 4096],
    pub watcher_pid: AtomicU32,
    pub state: AtomicU32,
    /// Terminal health flag, separate from payload ownership. Never reset after a failed handoff.
    pub handoff_failure: AtomicU32,
    pub sequence: AtomicU32,
    pub reentrant_count: AtomicU32,
    pub orderly_reason: AtomicU32,
    pub registration_failures: AtomicU32,
    pub worker_error: AtomicU32,
    pub worker_pid: AtomicU32,
    pub payload: UnsafeCell<Payload>,
    pub services: [ServiceSlot; SERVICE_SLOTS],
}
// All cross-process mutable fields have a fixed publisher/consumer state protocol.
unsafe impl Sync for Shared {}

pub(super) struct Owned(pub HANDLE);
unsafe impl Send for Owned {}
unsafe impl Sync for Owned {}
impl Drop for Owned {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

pub(super) struct Connection {
    pub map: Owned,
    pub request: Owned,
    pub done: Owned,
    pub ready: Owned,
    pub target: Owned,
    pub view: *mut Shared,
}
unsafe impl Send for Connection {}
unsafe impl Sync for Connection {}
impl Drop for Connection {
    fn drop(&mut self) {
        unsafe {
            let _ = UnmapViewOfFile(MEMORY_MAPPED_VIEW_ADDRESS {
                Value: self.view.cast(),
            });
        }
    }
}
impl Connection {
    pub fn shared(&self) -> &Shared {
        unsafe { &*self.view }
    }
    pub fn root(&self) -> PathBuf {
        use std::os::windows::ffi::OsStringExt;
        let s = self.shared();
        PathBuf::from(OsString::from_wide(&s.root[..s.root_len as usize]))
    }
    pub fn session(&self) -> Result<store::Session, String> {
        let s = self.shared();
        serde_json::from_slice(&s.metadata[..s.metadata_len as usize])
            .map_err(|_| "ipc-metadata-invalid".into())
    }
    pub fn create(session: &store::Session, root: &Path) -> Result<Self, String> {
        let meta = serde_json::to_vec(session).map_err(|_| "ipc-metadata-invalid")?;
        let root_words: Vec<_> = root.as_os_str().encode_wide().collect();
        if meta.len() > META_CAP || root_words.len() > 4096 {
            return Err("ipc-size-limit".into());
        }
        unsafe {
            let sa = SECURITY_ATTRIBUTES {
                nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
                lpSecurityDescriptor: std::ptr::null_mut(),
                bInheritHandle: TRUE,
            };
            let map = Owned(
                CreateFileMappingW(
                    INVALID_HANDLE_VALUE,
                    Some(&sa),
                    PAGE_READWRITE,
                    0,
                    size_of::<Shared>() as u32,
                    PCWSTR::null(),
                )
                .map_err(|_| "ipc-mapping-create")?,
            );
            let request = Owned(
                CreateEventW(Some(&sa), false, false, PCWSTR::null())
                    .map_err(|_| "ipc-event-create")?,
            );
            let done = Owned(
                CreateEventW(Some(&sa), false, false, PCWSTR::null())
                    .map_err(|_| "ipc-event-create")?,
            );
            let ready = Owned(
                CreateEventW(Some(&sa), true, false, PCWSTR::null())
                    .map_err(|_| "ipc-event-create")?,
            );
            let target = Owned(
                OpenProcess(
                    PROCESS_QUERY_INFORMATION
                        | PROCESS_VM_READ
                        | PROCESS_DUP_HANDLE
                        | PROCESS_SYNCHRONIZE,
                    true,
                    session.process_id,
                )
                .map_err(|_| "ipc-target-open")?,
            );
            let view = MapViewOfFile(map.0, FILE_MAP_ALL_ACCESS, 0, 0, size_of::<Shared>())
                .Value
                .cast::<Shared>();
            if view.is_null() {
                return Err("ipc-map-view".into());
            }
            if view as usize % std::mem::align_of::<Shared>() != 0 {
                let _ = UnmapViewOfFile(MEMORY_MAPPED_VIEW_ADDRESS { Value: view.cast() });
                return Err("ipc-map-alignment".into());
            }
            std::ptr::write_bytes(view, 0, 1);
            (*view).magic = MAGIC;
            (*view).version = 1;
            (*view).structure_bytes = size_of::<Shared>() as u32;
            (*view).target_pid = session.process_id;
            (*view).target_created = session.process_created_ticks;
            (*view).metadata_len = meta.len() as u32;
            (&mut (*view).metadata)[..meta.len()].copy_from_slice(&meta);
            (*view).root_len = root_words.len() as u32;
            (&mut (*view).root)[..root_words.len()].copy_from_slice(&root_words);
            Ok(Self {
                map,
                request,
                done,
                ready,
                target,
                view,
            })
        }
    }
    pub fn from_arguments(args: &[OsString], is_watcher: bool) -> Result<Self, String> {
        if args.len() != 7 {
            return Err("ipc-arguments-invalid".into());
        }
        let mut hs = Vec::new();
        for arg in &args[2..] {
            let n = arg
                .to_str()
                .and_then(|v| usize::from_str_radix(v, 16).ok())
                .filter(|n| *n > 0)
                .ok_or("ipc-handle-invalid")?;
            hs.push(HANDLE(n as *mut c_void));
        }
        let view = unsafe {
            MapViewOfFile(hs[0], FILE_MAP_ALL_ACCESS, 0, 0, size_of::<Shared>())
                .Value
                .cast::<Shared>()
        };
        if view.is_null() {
            return Err("ipc-map-view".into());
        }
        if view as usize % std::mem::align_of::<Shared>() != 0 {
            unsafe {
                let _ = UnmapViewOfFile(MEMORY_MAPPED_VIEW_ADDRESS { Value: view.cast() });
            }
            return Err("ipc-map-alignment".into());
        }
        let result = Self {
            map: Owned(hs[0]),
            request: Owned(hs[1]),
            done: Owned(hs[2]),
            ready: Owned(hs[3]),
            target: Owned(hs[4]),
            view,
        };
        let s = result.shared();
        if s.magic != MAGIC
            || s.version != 1
            || s.structure_bytes != size_of::<Shared>() as u32
            || s.metadata_len as usize > META_CAP
            || s.root_len as usize > 4096
            || s.root_len == 0
        {
            return Err("ipc-contract-invalid".into());
        }
        if unsafe { GetProcessId(result.target.0) } != s.target_pid
            || process_times(result.target.0)?.0 != s.target_created
        {
            return Err("ipc-target-identity".into());
        }
        let expected_parent = if is_watcher {
            s.target_pid
        } else {
            s.watcher_pid.load(Ordering::Acquire)
        };
        if expected_parent == 0 || parent_pid(std::process::id())? != expected_parent {
            return Err("ipc-parent-identity".into());
        }
        let current = std::env::current_exe()
            .map_err(|_| "ipc-current-image")?
            .canonicalize()
            .map_err(|_| "ipc-current-image")?;
        let target_path = image_path(result.target.0)?
            .canonicalize()
            .map_err(|_| "ipc-target-image")?;
        if current != target_path {
            return Err("ipc-target-image-mismatch".into());
        }
        let session = result.session()?;
        if session.process_id != s.target_pid
            || session.process_created_ticks != s.target_created
            || !store::valid_id(&session.session_id)
            || session.build_identity
                != crate::install_contract::embedded_identity()
                    .ok()
                    .flatten()
                    .map(|v| v.0)
        {
            return Err("ipc-build-identity".into());
        }
        let root = result.root();
        if !root.is_absolute()
            || root.file_name().and_then(|v| v.to_str()) != Some("native-crashes")
            || root != root.canonicalize().map_err(|_| "ipc-root-invalid")?
        {
            return Err("ipc-root-invalid".into());
        }
        if !is_watcher && s.state.load(Ordering::Acquire) != STATE_WORKING {
            return Err("ipc-worker-state".into());
        }
        Ok(result)
    }
    pub fn spawn(&self, mode: &str) -> Result<Owned, String> {
        let exe = std::env::current_exe().map_err(|_| "helper-image-missing")?;
        let app: Vec<u16> = exe.as_os_str().encode_wide().chain(Some(0)).collect();
        let handles = [
            self.map.0,
            self.request.0,
            self.done.0,
            self.ready.0,
            self.target.0,
        ];
        let numbers = handles
            .iter()
            .map(|h| format!("{:x}", h.0 as usize))
            .collect::<Vec<_>>()
            .join(" ");
        let mut cmd: Vec<u16> = format!("\"{}\" {mode} {numbers}", exe.display())
            .encode_utf16()
            .chain(Some(0))
            .collect();
        unsafe {
            struct InheritReset([HANDLE; 5]);
            impl Drop for InheritReset {
                fn drop(&mut self) {
                    for handle in self.0 {
                        unsafe {
                            let _ = SetHandleInformation(
                                handle,
                                HANDLE_FLAG_INHERIT.0,
                                HANDLE_FLAGS(0),
                            );
                        }
                    }
                }
            }
            let _reset = InheritReset(handles);
            for h in handles {
                SetHandleInformation(h, HANDLE_FLAG_INHERIT.0, HANDLE_FLAG_INHERIT)
                    .map_err(|_| "helper-handle-inherit")?;
            }
            let mut size = 0;
            let _ = InitializeProcThreadAttributeList(None, 1, None, &mut size);
            let mut storage = vec![0usize; size.div_ceil(size_of::<usize>())];
            let list = LPPROC_THREAD_ATTRIBUTE_LIST(storage.as_mut_ptr().cast());
            InitializeProcThreadAttributeList(Some(list), 1, None, &mut size)
                .map_err(|_| "helper-attribute-list")?;
            struct Attributes(LPPROC_THREAD_ATTRIBUTE_LIST);
            impl Drop for Attributes {
                fn drop(&mut self) {
                    unsafe {
                        DeleteProcThreadAttributeList(self.0);
                    }
                }
            }
            let _attributes = Attributes(list);
            UpdateProcThreadAttribute(
                list,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                Some(handles.as_ptr().cast()),
                size_of_val(&handles),
                None,
                None,
            )
            .map_err(|_| "helper-handle-list")?;
            let mut startup = STARTUPINFOEXW::default();
            startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
            startup.lpAttributeList = list;
            let mut process = PROCESS_INFORMATION::default();
            CreateProcessW(
                PCWSTR(app.as_ptr()),
                Some(PWSTR(cmd.as_mut_ptr())),
                None,
                None,
                true,
                CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT,
                None,
                PCWSTR::null(),
                &startup.StartupInfo,
                &mut process,
            )
            .map_err(|_| "helper-start-failed")?;
            let _ = CloseHandle(process.hThread);
            Ok(Owned(process.hProcess))
        }
    }
}

pub(super) fn process_times(handle: HANDLE) -> Result<(u64, u64), String> {
    let mut c = FILETIME::default();
    let mut e = FILETIME::default();
    let mut k = FILETIME::default();
    let mut u = FILETIME::default();
    unsafe {
        GetProcessTimes(handle, &mut c, &mut e, &mut k, &mut u)
            .map_err(|_| "process-times-unavailable")?;
    }
    Ok((ticks(c), ticks(e)))
}
pub(super) fn ticks(time: FILETIME) -> u64 {
    ((time.dwHighDateTime as u64) << 32) | time.dwLowDateTime as u64
}
pub(super) fn image_path(handle: HANDLE) -> Result<PathBuf, String> {
    let mut words = vec![0u16; 32768];
    let mut n = words.len() as u32;
    unsafe {
        QueryFullProcessImageNameW(
            handle,
            PROCESS_NAME_WIN32,
            PWSTR(words.as_mut_ptr()),
            &mut n,
        )
        .map_err(|_| "process-image-unavailable")?;
    }
    use std::os::windows::ffi::OsStringExt;
    Ok(PathBuf::from(OsString::from_wide(&words[..n as usize])))
}
fn parent_pid(pid: u32) -> Result<u32, String> {
    unsafe {
        let snap = Owned(
            CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
                .map_err(|_| "parent-snapshot-failed")?,
        );
        let mut entry = PROCESSENTRY32W {
            dwSize: size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        Process32FirstW(snap.0, &mut entry).map_err(|_| "parent-enumeration-failed")?;
        loop {
            if entry.th32ProcessID == pid {
                return Ok(entry.th32ParentProcessID);
            }
            if Process32NextW(snap.0, &mut entry).is_err() {
                break;
            }
        }
    }
    Err("parent-not-found".into())
}
pub(in crate::native_crash) fn replace_file(from: &Path, to: &Path) -> Result<(), String> {
    let f: Vec<_> = from.as_os_str().encode_wide().chain(Some(0)).collect();
    let t: Vec<_> = to.as_os_str().encode_wide().chain(Some(0)).collect();
    unsafe {
        MoveFileExW(
            PCWSTR(f.as_ptr()),
            PCWSTR(t.as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
        .map_err(|_| "metadata-rename-failed".into())
    }
}
pub(in crate::native_crash) fn validate_file_parent(
    file: &File,
    parent: &Path,
) -> Result<(), String> {
    let mut words = vec![0u16; 32768];
    let n = unsafe {
        GetFinalPathNameByHandleW(
            HANDLE(file.as_raw_handle()),
            &mut words,
            FILE_NAME_NORMALIZED,
        )
    } as usize;
    if n == 0 || n >= words.len() {
        return Err("dump-path-unavailable".into());
    }
    use std::os::windows::ffi::OsStringExt;
    let actual = PathBuf::from(OsString::from_wide(&words[..n]));
    if actual.parent() != Some(parent) {
        return Err("dump-path-rejected".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn captured_context_stays_sixteen_byte_aligned_inside_the_mapping() {
        let shared = std::mem::MaybeUninit::<Shared>::uninit();
        assert!(std::mem::align_of::<Shared>() >= 16);
        assert_eq!(std::mem::align_of::<AlignedContext>(), 16);
        let payload = unsafe { std::ptr::addr_of!((*shared.as_ptr()).payload) };
        // UnsafeCell has the layout of its contained payload; no uninitialized value is read.
        let context = unsafe { std::ptr::addr_of!((*(payload.cast::<Payload>())).context.0) };
        assert_eq!(context as usize % 16, 0);
        let worker_local = std::mem::MaybeUninit::<AlignedContext>::uninit();
        let worker_context = unsafe { std::ptr::addr_of!((*worker_local.as_ptr()).0) };
        assert_eq!(worker_context as usize % 16, 0);
    }
}
