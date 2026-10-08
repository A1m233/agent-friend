use super::super::store::{self, Session};
use super::ipc::*;
use std::{
    process::Child,
    sync::{
        atomic::{AtomicPtr, AtomicUsize, Ordering},
        Mutex, OnceLock,
    },
};
use windows::Win32::{
    Foundation::*,
    System::{Diagnostics::Debug::*, Threading::*},
};

struct Client {
    connection: Connection,
    watcher: Owned,
    session_id: String,
}
static CLIENT: AtomicPtr<Client> = AtomicPtr::new(std::ptr::null_mut());
static PREVIOUS: AtomicUsize = AtomicUsize::new(0);
static REGISTER: Mutex<()> = Mutex::new(());
static FAILURES: OnceLock<Mutex<Vec<String>>> = OnceLock::new();

fn failure(code: &str) {
    if let Ok(mut errors) = FAILURES.get_or_init(|| Mutex::new(Vec::new())).lock() {
        errors.push(code.into());
    }
}
pub(in crate::native_crash) fn current_failures() -> Vec<String> {
    let mut result = FAILURES
        .get()
        .and_then(|v| v.lock().ok())
        .map(|v| v.clone())
        .unwrap_or_default();
    let ptr = CLIENT.load(Ordering::Acquire);
    if !ptr.is_null() && unsafe { WaitForSingleObject((*ptr).watcher.0, 0) } == WAIT_OBJECT_0 {
        result.push("watcher-exited".into());
    }
    if let Some((_, _, code)) = current_handoff_failure() {
        result.push(code.into());
    }
    result
}

/// Healthy-process projection only; the fault handler does not allocate or call this function.
pub(in crate::native_crash) fn current_handoff_failure() -> Option<(String, String, &'static str)> {
    let ptr = CLIENT.load(Ordering::Acquire);
    if ptr.is_null() {
        return None;
    }
    let client = unsafe { &*ptr };
    let shared = client.connection.shared();
    let code = handoff_failure_code(shared.handoff_failure.load(Ordering::Acquire))?;
    Some((
        client.session_id.clone(),
        format!(
            "{}-{:06}",
            client.session_id,
            shared.sequence.load(Ordering::Acquire)
        ),
        code,
    ))
}

pub(in crate::native_crash) fn start(session_id: &str) {
    start_config(session_id, 0);
}

#[cfg(debug_assertions)]
pub(super) fn start_fixture(session_id: &str, flags: u32) {
    start_config(session_id, if cfg!(debug_assertions) { flags } else { 0 });
}

fn start_config(session_id: &str, flags: u32) {
    if !CLIENT.load(Ordering::Acquire).is_null() {
        return;
    }
    let root = crate::paths::native_crash_dir();
    if let Err(e) = store::initialize(&root) {
        failure(&e);
        return;
    }
    let root = match root.canonicalize() {
        Ok(v) => v,
        Err(_) => {
            failure("storage-path-unavailable");
            return;
        }
    };
    recover_abandoned(&root);
    for e in store::prune(&root, false) {
        failure(&e);
    }
    let identity = crate::install_contract::embedded_identity();
    let created = unsafe { process_times(GetCurrentProcess()).map(|v| v.0).unwrap_or(0) };
    let mut session = Session {
        schema_version: 1,
        session_id: session_id.into(),
        process_id: std::process::id(),
        process_created_ticks: created,
        recorded_at: store::now(),
        build_identity: identity
            .as_ref()
            .ok()
            .and_then(|v| v.as_ref())
            .map(|v| v.0.clone()),
        identity_status: match identity {
            Ok(Some(_)) => "embedded",
            Ok(None) => "development",
            Err(_) => "invalid",
        }
        .into(),
        capture_status: "starting".into(),
        watcher_pid: None,
        watcher_created_ticks: None,
        completed: false,
        failures: vec![],
    };
    if let Err(e) = store::save_session(&root, &session) {
        failure(&e);
        return;
    }
    let launch = (|| -> Result<Client, String> {
        let connection = Connection::create(&session, &root)?;
        unsafe {
            (*connection.view).fixture_flags = flags;
        }
        if cfg!(debug_assertions) && flags & 1 != 0 {
            return Err("helper-start-failed".into());
        }
        let watcher = connection.spawn("--af-native-watch-v1")?;
        let wait = unsafe { WaitForMultipleObjects(&[connection.ready.0, watcher.0], false, 2500) };
        if wait != WAIT_OBJECT_0 {
            unsafe {
                let _ = TerminateProcess(watcher.0, 70);
                let _ = WaitForSingleObject(watcher.0, 1000);
            }
            return Err("watcher-handshake-failed".into());
        }
        Ok(Client {
            connection,
            watcher,
            session_id: session_id.into(),
        })
    })();
    match launch {
        Ok(client) => unsafe {
            // Leak preallocated storage deliberately: native callbacks may run during TLS teardown.
            let ptr = Box::into_raw(Box::new(client));
            CLIENT.store(ptr, Ordering::Release);
            let previous = SetUnhandledExceptionFilter(Some(unhandled));
            PREVIOUS.store(previous.map_or(0, |f| f as usize), Ordering::Release);
            if AddVectoredExceptionHandler(1, Some(stack_overflow)).is_null() {
                failure("stack-overflow-handler-unavailable");
            }
            let mut reserve = 64 * 1024;
            if SetThreadStackGuarantee(&mut reserve).is_err() {
                failure("stack-guarantee-unavailable");
            }
        },
        Err(error) => {
            failure(&error);
            session.capture_status = "unavailable".into();
            session.failures.push(error);
            let _ = store::save_session(&root, &session);
        }
    }
}

#[cfg(debug_assertions)]
pub(super) fn fixture_kill_watcher() {
    if !cfg!(debug_assertions) {
        return;
    }
    let ptr = CLIENT.load(Ordering::Acquire);
    if !ptr.is_null() {
        unsafe {
            let _ = TerminateProcess((*ptr).watcher.0, 72);
            let _ = WaitForSingleObject((*ptr).watcher.0, 1000);
        }
    }
}

#[cfg(debug_assertions)]
pub(super) fn fixture_sequence() -> u32 {
    let ptr = CLIENT.load(Ordering::Acquire);
    if ptr.is_null() {
        return 0;
    }
    unsafe { (*ptr).connection.shared().sequence.load(Ordering::Acquire) }
}

pub(in crate::native_crash) fn orderly_exit(reason: &str) {
    let ptr = CLIENT.load(Ordering::Acquire);
    if ptr.is_null() {
        return;
    }
    let code = match reason {
        "normal-return" => 1,
        "tauri-exit" => 2,
        "second-instance" => 3,
        "startup-rejected" => 4,
        _ => 0,
    };
    unsafe {
        (*ptr)
            .connection
            .shared()
            .orderly_reason
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |old| {
                Some(reason_after(old, code))
            })
            .ok();
        let _ = SetEvent((*ptr).connection.request.0);
    }
}

fn reason_after(previous: u32, requested: u32) -> u32 {
    if previous == 0 || (previous == 1 && requested > 1) {
        requested
    } else {
        previous
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicU32;
    #[test]
    fn generic_return_does_not_erase_specific_exit_reason() {
        for specific in [2, 3, 4] {
            assert_eq!(reason_after(specific, 1), specific);
        }
        assert_eq!(reason_after(3, 2), 3);
        assert_eq!(reason_after(0, 1), 1);
        assert_eq!(reason_after(1, 4), 4);
    }

    #[test]
    fn failed_handoffs_disable_capture_without_reclaiming_payload_state() {
        for (waited, expected) in [
            (WAIT_TIMEOUT, HANDOFF_TIMEOUT),
            (WAIT_FAILED, HANDOFF_WAIT_FAILED),
            (WAIT_EVENT(1), HANDOFF_WATCHER_EXITED),
            (WAIT_OBJECT_0, HANDOFF_PROTOCOL_FAILED),
        ] {
            let state = AtomicU32::new(STATE_WORKING);
            let failure = AtomicU32::new(0);
            complete_handoff(&state, &failure, waited);
            assert_eq!(state.load(Ordering::Acquire), STATE_WORKING);
            assert_eq!(failure.load(Ordering::Acquire), expected);
            // A late completion cannot rearm a failed channel or replace its original cause.
            state.store(STATE_DONE, Ordering::Release);
            complete_handoff(&state, &failure, WAIT_OBJECT_0);
            assert_eq!(state.load(Ordering::Acquire), STATE_DONE);
            assert_eq!(failure.load(Ordering::Acquire), expected);
        }
        let state = AtomicU32::new(STATE_DONE);
        let failure = AtomicU32::new(0);
        complete_handoff(&state, &failure, WAIT_OBJECT_0);
        assert_eq!(state.load(Ordering::Acquire), STATE_IDLE);
    }
}

pub(in crate::native_crash) fn panic_observed() {
    unsafe {
        handoff(3, std::ptr::null());
    }
}

unsafe extern "system" fn unhandled(info: *const EXCEPTION_POINTERS) -> i32 {
    handoff(1, info);
    let previous = PREVIOUS.load(Ordering::Acquire);
    if previous != 0 {
        let handler: unsafe extern "system" fn(*const EXCEPTION_POINTERS) -> i32 =
            std::mem::transmute(previous);
        return handler(info);
    }
    EXCEPTION_CONTINUE_SEARCH
}
unsafe extern "system" fn stack_overflow(info: *mut EXCEPTION_POINTERS) -> i32 {
    if !info.is_null()
        && !(*info).ExceptionRecord.is_null()
        && (*(*info).ExceptionRecord).ExceptionCode.0 as u32 == 0xc00000fd
    {
        handoff(2, info);
    }
    EXCEPTION_CONTINUE_SEARCH
}

/// Fault-thread code: fixed copies, atomics and pre-created kernel objects only.
unsafe fn handoff(kind: u32, info: *const EXCEPTION_POINTERS) {
    let ptr = CLIENT.load(Ordering::Acquire);
    if ptr.is_null() {
        return;
    }
    let client = &*ptr;
    let s = client.connection.shared();
    // A timed-out observer may still own/read this payload. Disable capture without rearming it.
    if s.handoff_failure.load(Ordering::Acquire) != 0 {
        return;
    }
    if s.state
        .compare_exchange(
            STATE_IDLE,
            STATE_WRITING,
            Ordering::AcqRel,
            Ordering::Acquire,
        )
        .is_err()
    {
        s.reentrant_count.fetch_add(1, Ordering::Relaxed);
        return;
    }
    if s.sequence.load(Ordering::Relaxed) >= 32 {
        s.registration_failures.fetch_add(1, Ordering::Relaxed);
        s.state.store(STATE_IDLE, Ordering::Release);
        return;
    }
    let p = s.payload.get();
    // A stack-overflow VEH may be followed by UEF for that same fault; keep its original context.
    if kind == 1
        && !info.is_null()
        && !(*info).ExceptionRecord.is_null()
        && (*p).kind == 2
        && (*p).thread_id == GetCurrentThreadId()
        && (*p).record.ExceptionCode == (*(*info).ExceptionRecord).ExceptionCode
    {
        s.state.store(STATE_IDLE, Ordering::Release);
        return;
    }
    (*p).kind = kind;
    (*p).thread_id = GetCurrentThreadId();
    (*p).sequence = s.sequence.fetch_add(1, Ordering::Relaxed) + 1;
    if !info.is_null() && !(*info).ExceptionRecord.is_null() && !(*info).ContextRecord.is_null() {
        std::ptr::copy_nonoverlapping((*info).ExceptionRecord, &mut (*p).record, 1);
        (*p).record.ExceptionRecord = std::ptr::null_mut();
        std::ptr::copy_nonoverlapping((*info).ContextRecord, &mut (*p).context.0, 1);
    } else {
        std::ptr::write_bytes(&mut (*p).record, 0, 1);
        RtlCaptureContext(&mut (*p).context.0);
        // Synthetic user-stream marker, never represented as an observed OS exception.
        (*p).record.ExceptionCode = NTSTATUS(0xe041464e_u32 as i32);
        #[cfg(target_arch = "x86_64")]
        {
            (*p).record.ExceptionAddress = (*p).context.0.Rip as *mut _;
        }
    }
    if ResetEvent(client.connection.done.0).is_err() {
        mark_handoff_failure(&s.handoff_failure, HANDOFF_NOTIFY_FAILED);
        return;
    }
    s.state.store(STATE_REQUEST, Ordering::Release);
    if SetEvent(client.connection.request.0).is_err() {
        mark_handoff_failure(&s.handoff_failure, HANDOFF_NOTIFY_FAILED);
        return;
    }
    let waited = WaitForMultipleObjects(&[client.connection.done.0, client.watcher.0], false, 8000);
    complete_handoff(&s.state, &s.handoff_failure, waited);
    if s.handoff_failure.load(Ordering::Acquire) != 0 {
        let _ = SetEvent(client.connection.request.0);
    }
}

fn mark_handoff_failure(failure: &std::sync::atomic::AtomicU32, code: u32) {
    let _ = failure.compare_exchange(0, code, Ordering::AcqRel, Ordering::Acquire);
}

/// Failed waits never mutate payload ownership; a late watcher is free to finish its own state.
fn complete_handoff(
    state: &std::sync::atomic::AtomicU32,
    failure: &std::sync::atomic::AtomicU32,
    waited: WAIT_EVENT,
) {
    if waited == WAIT_OBJECT_0
        && state.load(Ordering::Acquire) == STATE_DONE
        && failure.load(Ordering::Acquire) == 0
    {
        state.store(STATE_IDLE, Ordering::Release);
        return;
    }
    let code = if waited == WAIT_TIMEOUT {
        HANDOFF_TIMEOUT
    } else if waited == WAIT_FAILED {
        HANDOFF_WAIT_FAILED
    } else if waited.0 == WAIT_OBJECT_0.0 + 1 {
        HANDOFF_WATCHER_EXITED
    } else {
        HANDOFF_PROTOCOL_FAILED
    };
    mark_handoff_failure(failure, code);
}

pub(in crate::native_crash) fn register_service(child: &Child, role: &str, generation: u64) {
    use std::os::windows::io::AsRawHandle;
    let ptr = CLIENT.load(Ordering::Acquire);
    if ptr.is_null() {
        return;
    }
    let code = match role {
        "bridge" => 1,
        "voice" => 2,
        _ => return,
    };
    let Ok(_lock) = REGISTER.lock() else {
        return;
    };
    unsafe {
        let client = &*ptr;
        let s = client.connection.shared();
        let Some(slot) = s.services.iter().find(|slot| {
            let old = slot.state.load(Ordering::Acquire);
            (old == 0 || old == 4)
                && slot
                    .state
                    .compare_exchange(old, 1, Ordering::AcqRel, Ordering::Acquire)
                    .is_ok()
        }) else {
            s.registration_failures.fetch_add(1, Ordering::Relaxed);
            return;
        };
        let source = HANDLE(child.as_raw_handle());
        let mut target = HANDLE::default();
        let created = match process_times(source) {
            Ok(v) => v.0,
            Err(_) => {
                slot.state.store(0, Ordering::Release);
                s.registration_failures.fetch_add(1, Ordering::Relaxed);
                return;
            }
        };
        if DuplicateHandle(
            GetCurrentProcess(),
            source,
            client.watcher.0,
            &mut target,
            (PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE).0,
            false,
            DUPLICATE_HANDLE_OPTIONS(0),
        )
        .is_err()
        {
            slot.state.store(0, Ordering::Release);
            s.registration_failures.fetch_add(1, Ordering::Relaxed);
            return;
        }
        *slot.value.get() = ServicePayload {
            handle: target.0 as usize,
            role: code,
            generation,
            pid: child.id(),
            created,
        };
        slot.state.store(2, Ordering::Release);
        let _ = SetEvent(client.connection.request.0);
    }
}

fn is_alive(pid: u32, created: u64) -> bool {
    unsafe {
        let Ok(handle) = OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
            false,
            pid,
        ) else {
            return false;
        };
        let handle = Owned(handle);
        process_times(handle.0).is_ok_and(|v| v.0 == created)
            && WaitForSingleObject(handle.0, 0) != WAIT_OBJECT_0
    }
}
fn recover_abandoned(root: &std::path::Path) {
    for (_, value) in store::sessions(root) {
        let Ok(mut session) = value else {
            continue;
        };
        if session.completed
            || is_alive(session.process_id, session.process_created_ticks)
            || session
                .watcher_pid
                .zip(session.watcher_created_ticks)
                .is_some_and(|(p, c)| is_alive(p, c))
        {
            continue;
        }
        let incident = store::Incident::new(&session, 999999, "unexpected-end-unknown");
        session
            .failures
            .extend(store::recover_interrupted(root, &session.session_id));
        if store::save_incident(root, &incident).is_ok() {
            session.completed = true;
            session
                .failures
                .push("observer-and-process-ended-without-result".into());
            let _ = store::save_session(root, &session);
        }
        // Only this dead session's unfinished files can be recovered; never touch system dumps.
        if let Ok(entries) = std::fs::read_dir(root.join("dumps")) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if name.starts_with(&format!("{}-", session.session_id))
                    && name.ends_with(".partial")
                {
                    let _ = std::fs::remove_file(entry.path());
                }
            }
        }
    }
}
