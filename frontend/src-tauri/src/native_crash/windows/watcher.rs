use super::super::store::{self, Dump, Incident, ServiceExit};
use super::ipc::*;
use std::{
    mem::size_of,
    sync::atomic::Ordering,
    time::{Duration, Instant},
};
use windows::Win32::{
    Foundation::*,
    System::{Diagnostics::ToolHelp::*, Threading::*},
};

struct Observed {
    handle: Owned,
    slot: usize,
    result: ServiceExit,
}

fn register_services(connection: &Connection, active: &mut Vec<Observed>) {
    for (index, slot) in connection.shared().services.iter().enumerate() {
        if slot
            .state
            .compare_exchange(2, 3, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            continue;
        }
        let value = unsafe { *slot.value.get() };
        let handle = Owned(HANDLE(value.handle as *mut _));
        if unsafe { GetProcessId(handle.0) } != value.pid
            || process_times(handle.0).map_or(true, |v| v.0 != value.created)
            || !matches!(value.role, 1 | 2)
        {
            connection
                .shared()
                .registration_failures
                .fetch_add(1, Ordering::Relaxed);
            slot.state.store(4, Ordering::Release);
            continue;
        }
        active.push(Observed {
            handle,
            slot: index,
            result: ServiceExit {
                role: if value.role == 1 { "bridge" } else { "voice" }.into(),
                generation: value.generation,
                process_id: value.pid,
                process_created_ticks: value.created,
                exit_code: None,
                exit_time_ticks: None,
                order: "order-unknown".into(),
            },
        });
    }
}

fn collect_exited(
    connection: &Connection,
    active: &mut Vec<Observed>,
    finished: &mut Vec<ServiceExit>,
    shell_exit: Option<u64>,
) {
    let mut index = 0;
    while index < active.len() {
        if unsafe { WaitForSingleObject(active[index].handle.0, 0) } != WAIT_OBJECT_0 {
            index += 1;
            continue;
        }
        let mut item = active.remove(index);
        let mut code = 0;
        if unsafe { GetExitCodeProcess(item.handle.0, &mut code) }.is_ok() {
            item.result.exit_code = Some(code);
        }
        item.result.exit_time_ticks = process_times(item.handle.0)
            .ok()
            .map(|t| t.1)
            .filter(|t| *t > 0);
        item.result.order = order(item.result.exit_time_ticks, shell_exit);
        if finished.len() >= 100 {
            finished.remove(0);
            connection
                .shared()
                .registration_failures
                .fetch_add(1, Ordering::Relaxed);
        }
        finished.push(item.result);
        connection.shared().services[item.slot]
            .state
            .store(4, Ordering::Release);
    }
}
fn order(service: Option<u64>, shell: Option<u64>) -> String {
    match (service, shell) {
        (Some(a), Some(b)) if a < b => "service-first",
        (Some(a), Some(b)) if a > b => "shell-first",
        (Some(_), Some(_)) => "simultaneous",
        _ => "order-unknown",
    }
    .into()
}

pub(super) fn run(connection: Connection) -> i32 {
    let root = connection.root();
    let s = connection.shared();
    let mut session = match connection.session() {
        Ok(v) => v,
        Err(_) => return 65,
    };
    s.watcher_pid.store(std::process::id(), Ordering::Release);
    session.watcher_pid = Some(std::process::id());
    session.watcher_created_ticks = unsafe { process_times(GetCurrentProcess()).ok().map(|v| v.0) };
    session.capture_status = "ready".into();
    if store::save_session(&root, &session).is_err() {
        return 66;
    }
    unsafe {
        let _ = SetEvent(connection.ready.0);
    }
    let mut active = Vec::new();
    let mut finished = Vec::new();
    let mut observed_failures = 0;
    loop {
        register_services(&connection, &mut active);
        let prior = finished.len();
        collect_exited(&connection, &mut active, &mut finished, None);
        if finished.len() > prior {
            let mut event = Incident::new(&session, 900000 + finished.len() as u32, "service-exit");
            event.services = finished[prior..].to_vec();
            for service in &mut event.services {
                service.order = "service-first".into();
            }
            if store::save_incident(&root, &event).is_err() {
                session
                    .failures
                    .push("service-evidence-write-failed".into());
            }
        }
        if s.state
            .compare_exchange(
                STATE_REQUEST,
                STATE_WORKING,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .is_ok()
        {
            if cfg!(debug_assertions) && s.fixture_flags & 8 != 0 {
                // Whole-observer delay, deliberately outside the worker's six-second budget.
                std::thread::sleep(Duration::from_millis(9500));
            }
            capture(&connection, &session);
            s.state.store(STATE_DONE, Ordering::Release);
            unsafe {
                let _ = SetEvent(connection.done.0);
            }
        }
        if let Some(code) = handoff_failure_code(s.handoff_failure.load(Ordering::Acquire)) {
            if !session.failures.iter().any(|v| v == code) {
                session.capture_status = "degraded".into();
                session.failures.push(code.into());
                let _ = store::save_session(&root, &session);
            }
        }
        let current_failures = s.registration_failures.load(Ordering::Relaxed);
        if current_failures != observed_failures {
            observed_failures = current_failures;
            session.capture_status = "degraded".into();
            if !session
                .failures
                .iter()
                .any(|v| v == "evidence-registration-limit-or-failure")
            {
                session
                    .failures
                    .push("evidence-registration-limit-or-failure".into());
            }
            let _ = store::save_session(&root, &session);
        }
        if unsafe { WaitForSingleObject(connection.target.0, 0) } == WAIT_OBJECT_0 {
            break;
        }
        let mut handles = vec![connection.request.0, connection.target.0];
        handles.extend(active.iter().map(|v| v.handle.0));
        if unsafe { WaitForMultipleObjects(&handles, false, 30000) } == WAIT_FAILED {
            session.failures.push("observer-wait-failed".into());
            break;
        }
    }
    let shell_exit = process_times(connection.target.0)
        .ok()
        .map(|t| t.1)
        .filter(|t| *t > 0);
    let deadline = Instant::now() + Duration::from_millis(1500);
    loop {
        register_services(&connection, &mut active);
        collect_exited(&connection, &mut active, &mut finished, shell_exit);
        if active.is_empty() || Instant::now() >= deadline {
            break;
        }
        let handles: Vec<_> = active.iter().map(|v| v.handle.0).collect();
        unsafe {
            let _ = WaitForMultipleObjects(&handles, false, 100);
        }
    }
    for item in active {
        finished.push(item.result);
    }
    for service in &mut finished {
        service.order = order(service.exit_time_ticks, shell_exit);
    }
    let mut event = Incident::new(&session, 999998, "process-exit");
    let mut code = 0;
    if unsafe { WaitForSingleObject(connection.target.0, 0) } == WAIT_OBJECT_0
        && unsafe { GetExitCodeProcess(connection.target.0, &mut code) }.is_ok()
    {
        event.exit_code = Some(code);
    }
    event.exit_time_ticks = shell_exit;
    event.services = finished;
    event.reentrant_count = s.reentrant_count.load(Ordering::Relaxed);
    event.orderly_reason = match s.orderly_reason.load(Ordering::Acquire) {
        1 => Some("normal-return"),
        2 => Some("tauri-exit"),
        3 => Some("second-instance"),
        4 => Some("startup-rejected"),
        _ => None,
    }
    .map(String::from);
    if event.orderly_reason.is_some() && event.exit_code == Some(0) {
        event.dump = Dump::unavailable("orderly-exit");
    } else {
        event.dump = Dump::unavailable("exit-only-no-exception-context");
    }
    if store::save_incident(&root, &event).is_err() {
        session.failures.push("exit-evidence-write-failed".into());
    }
    session.completed = true;
    session.failures.extend(store::prune(&root, false));
    let _ = store::save_session(&root, &session);
    0
}

fn capture(connection: &Connection, session: &store::Session) {
    let s = connection.shared();
    let p = unsafe { &*s.payload.get() };
    let root = connection.root();
    let mut event = Incident::new(
        session,
        p.sequence,
        match p.kind {
            1 => "native-exception",
            2 => "stack-overflow-observed",
            _ => "rust-panic",
        },
    );
    event.thread_id = Some(p.thread_id);
    event.reentrant_count = s.reentrant_count.load(Ordering::Relaxed);
    if p.kind != 3 {
        event.exception_code = Some(format!("0x{:08x}", p.record.ExceptionCode.0 as u32));
        event.exception_address = Some(format!("0x{:x}", p.record.ExceptionAddress as usize));
    }
    if let Some((name, rva)) = fault_module(s.target_pid, p.record.ExceptionAddress as usize) {
        event.fault_module = Some(name);
        event.fault_rva = Some(format!("0x{rva:x}"));
    }
    event.dump.status = "pending".into();
    event.dump.error_code = None;
    if store::save_incident(&root, &event).is_err() {
        s.worker_error.store(3, Ordering::Release);
        return;
    }
    if reject_expired(connection, &mut event) {
        return;
    }
    let retention = store::prune(&root, true);
    if !retention.is_empty() {
        event.dump.status = "failed".into();
        event.dump.error_code = Some("retention-reservation-failed".into());
        let _ = store::save_incident(&root, &event);
        return;
    }
    s.worker_error.store(0, Ordering::Release);
    if reject_expired(connection, &mut event) {
        return;
    }
    let result = connection.spawn("--af-native-dump-v1");
    let worker_error = match result {
        Ok(worker) => {
            if unsafe { WaitForSingleObject(worker.0, 6000) } != WAIT_OBJECT_0 {
                unsafe {
                    let _ = TerminateProcess(worker.0, 71);
                    let _ = WaitForSingleObject(worker.0, 500);
                }
                Some("dump-timeout")
            } else {
                let mut code = 1;
                unsafe {
                    let _ = GetExitCodeProcess(worker.0, &mut code);
                }
                if code == 0 {
                    None
                } else {
                    Some(match s.worker_error.load(Ordering::Acquire) {
                        1 => "dump-size-limit",
                        2 => "dump-timeout",
                        3 => "dump-write-failed",
                        5 => "capture-context-expired",
                        _ => "dump-generation-failed",
                    })
                }
            }
        }
        Err(_) => Some("dump-worker-unavailable"),
    };
    let error = if s.handoff_failure.load(Ordering::Acquire) != 0 {
        Some("capture-context-expired")
    } else {
        worker_error
    };
    if let Some(error) = error {
        event.dump.status = "failed".into();
        event.dump.error_code = Some(error.into());
        let _ = store::save_incident(&root, &event);
        let _ = std::fs::remove_file(
            root.join("dumps")
                .join(format!("{}.partial", event.incident_id)),
        );
        let _ = std::fs::remove_file(
            root.join("dumps")
                .join(format!("{}.dmp", event.incident_id)),
        );
    }
}

fn reject_expired(connection: &Connection, event: &mut Incident) -> bool {
    if connection.shared().handoff_failure.load(Ordering::Acquire) == 0 {
        return false;
    }
    event.dump.status = "failed".into();
    event.dump.error_code = Some("capture-context-expired".into());
    let _ = store::save_incident(&connection.root(), event);
    true
}

fn fault_module(pid: u32, address: usize) -> Option<(String, usize)> {
    unsafe {
        let snap =
            Owned(CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid).ok()?);
        let mut module = MODULEENTRY32W {
            dwSize: size_of::<MODULEENTRY32W>() as u32,
            ..Default::default()
        };
        Module32FirstW(snap.0, &mut module).ok()?;
        loop {
            let base = module.modBaseAddr as usize;
            if address >= base && address < base.saturating_add(module.modBaseSize as usize) {
                let len = module
                    .szModule
                    .iter()
                    .position(|v| *v == 0)
                    .unwrap_or(module.szModule.len());
                return Some((
                    String::from_utf16_lossy(&module.szModule[..len]),
                    address - base,
                ));
            }
            if Module32NextW(snap.0, &mut module).is_err() {
                break;
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exit_order_never_uses_poll_order() {
        assert_eq!(order(Some(3), Some(5)), "service-first");
        assert_eq!(order(Some(7), Some(5)), "shell-first");
        assert_eq!(order(Some(5), Some(5)), "simultaneous");
        assert_eq!(order(None, Some(5)), "order-unknown");
    }
}
