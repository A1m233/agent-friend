use super::super::store::{self, Dump, Incident};
use super::ipc::*;
use std::{
    ffi::c_void,
    fs::OpenOptions,
    io::{Seek, SeekFrom, Write},
    os::windows::io::AsRawHandle,
    sync::atomic::Ordering,
    time::{Duration, Instant},
};
use windows::{
    core::{BOOL, HRESULT},
    Win32::{
        Foundation::*,
        System::{Diagnostics::Debug::*, Threading::GetCurrentProcessId},
    },
};

struct Output {
    file: std::fs::File,
    started: Instant,
    error: u32,
    handoff_failure: *const std::sync::atomic::AtomicU32,
}

unsafe extern "system" fn callback(
    param: *mut c_void,
    input: *const MINIDUMP_CALLBACK_INPUT,
    output: *mut MINIDUMP_CALLBACK_OUTPUT,
) -> BOOL {
    if param.is_null() || input.is_null() || output.is_null() {
        return FALSE;
    }
    let state = &mut *param.cast::<Output>();
    if (*state.handoff_failure).load(Ordering::Acquire) != 0 {
        state.error = 5;
        return FALSE;
    }
    let kind = (*input).CallbackType as i32;
    if kind == IoStartCallback.0 {
        (*output).Anonymous.Status = S_FALSE;
        return TRUE;
    }
    if kind == IoFinishCallback.0 {
        (*output).Anonymous.Status = S_OK;
        return TRUE;
    }
    if kind == CancelCallback.0 {
        let elapsed = state.started.elapsed() > Duration::from_millis(5500);
        if elapsed {
            state.error = 2;
        }
        (*output).Anonymous.Anonymous2 = MINIDUMP_CALLBACK_OUTPUT_0_1 {
            CheckCancel: TRUE,
            Cancel: BOOL::from(elapsed),
        };
        return TRUE;
    }
    if kind == IoWriteAllCallback.0 {
        let request = (*input).Anonymous.Io;
        if request
            .Offset
            .checked_add(request.BufferBytes as u64)
            .map_or(true, |end| end > store::MAX_DUMP_BYTES)
        {
            state.error = 1;
            return FALSE;
        }
        if state.started.elapsed() > Duration::from_millis(5500) {
            state.error = 2;
            return FALSE;
        }
        if request.Buffer.is_null() && request.BufferBytes > 0 {
            state.error = 3;
            return FALSE;
        }
        let bytes = if request.BufferBytes == 0 {
            &[]
        } else {
            std::slice::from_raw_parts(request.Buffer.cast::<u8>(), request.BufferBytes as usize)
        };
        if state
            .file
            .seek(SeekFrom::Start(request.Offset))
            .and_then(|_| state.file.write_all(bytes))
            .is_err()
        {
            state.error = 3;
            return FALSE;
        }
        (*output).Anonymous.Status = HRESULT(0);
        return TRUE;
    }
    TRUE
}

pub(super) fn run(connection: Connection) -> i32 {
    let s = connection.shared();
    if cfg!(debug_assertions) && s.fixture_flags & 2 != 0 {
        std::thread::sleep(Duration::from_secs(20));
    }
    s.worker_pid
        .store(unsafe { GetCurrentProcessId() }, Ordering::Release);
    let root = connection.root();
    let session = match connection.session() {
        Ok(s) => s,
        Err(_) => return 65,
    };
    let p = unsafe { &*s.payload.get() };
    let id = format!("{}-{:06}", session.session_id, p.sequence);
    let incident_path = root.join("incidents").join(format!("{id}.json"));
    let mut incident: Incident = match store::read_json(&incident_path) {
        Ok(v) => v,
        Err(_) => return 66,
    };
    if incident.incident_id != id
        || incident.process_id != s.target_pid
        || incident.process_created_ticks != s.target_created
    {
        return 66;
    }
    let partial = root.join("dumps").join(format!("{id}.partial"));
    let path = root.join("dumps").join(format!("{id}.dmp"));
    let capture = (|| -> Result<Dump, u32> {
        if s.handoff_failure.load(Ordering::Acquire) != 0 {
            return Err(5);
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&partial)
            .map_err(|_| 3u32)?;
        if cfg!(debug_assertions) && s.fixture_flags & 4 != 0 {
            return Err(3);
        }
        let mut state = Output {
            file,
            started: Instant::now(),
            error: 0,
            handoff_failure: &s.handoff_failure,
        };
        let mut record = p.record;
        let mut context = AlignedContext(p.context.0);
        let mut pointers = EXCEPTION_POINTERS {
            ExceptionRecord: &mut record,
            ContextRecord: &mut context.0,
        };
        let exception = MINIDUMP_EXCEPTION_INFORMATION {
            ThreadId: p.thread_id,
            ExceptionPointers: &mut pointers,
            ClientPointers: FALSE,
        };
        let callback_info = MINIDUMP_CALLBACK_INFORMATION {
            CallbackRoutine: Some(callback),
            CallbackParam: (&mut state as *mut Output).cast(),
        };
        let flags = MiniDumpWithThreadInfo
            | MiniDumpWithUnloadedModules
            | MiniDumpWithIndirectlyReferencedMemory
            | MiniDumpWithModuleHeaders;
        let result = unsafe {
            MiniDumpWriteDump(
                connection.target.0,
                s.target_pid,
                HANDLE(state.file.as_raw_handle()),
                flags,
                Some(&exception),
                None,
                Some(&callback_info),
            )
        };
        if result.is_err() {
            return Err(if state.error != 0 { state.error } else { 4 });
        }
        if s.handoff_failure.load(Ordering::Acquire) != 0 {
            return Err(5);
        }
        state.file.sync_all().map_err(|_| 3u32)?;
        let (size, hash) = store::hash_file(&mut state.file).map_err(|_| 4u32)?;
        if size < 32 {
            return Err(4);
        }
        drop(state);
        if s.handoff_failure.load(Ordering::Acquire) != 0 {
            return Err(5);
        }
        replace_file(&partial, &path).map_err(|_| 3u32)?;
        Ok(Dump {
            status: "captured".into(),
            size_bytes: Some(size),
            sha256: Some(hash),
            error_code: None,
        })
    })();
    match capture {
        Ok(dump) => {
            if s.handoff_failure.load(Ordering::Acquire) != 0 {
                s.worker_error.store(5, Ordering::Release);
                let _ = std::fs::remove_file(&path);
                return 68;
            }
            incident.dump = dump;
            if store::save_incident(&root, &incident).is_err() {
                s.worker_error.store(3, Ordering::Release);
                return 67;
            }
            0
        }
        Err(code) => {
            s.worker_error.store(code, Ordering::Release);
            let _ = std::fs::remove_file(&partial);
            let _ = std::fs::remove_file(&path);
            68
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicU32;
    #[test]
    fn expired_handoff_rejects_minidump_io_before_writing_memory() {
        let path = std::env::temp_dir().join(format!(
            "af-expired-dump-{}-{}",
            std::process::id(),
            rand::random::<u64>()
        ));
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
            .unwrap();
        let failed = AtomicU32::new(HANDOFF_TIMEOUT);
        let mut state = Output {
            file,
            started: Instant::now(),
            error: 0,
            handoff_failure: &failed,
        };
        let bytes = b"memory must never be published";
        let mut input = MINIDUMP_CALLBACK_INPUT {
            CallbackType: IoWriteAllCallback.0 as u32,
            ..Default::default()
        };
        input.Anonymous.Io = MINIDUMP_IO_CALLBACK {
            Handle: HANDLE(state.file.as_raw_handle()),
            Offset: 0,
            Buffer: bytes.as_ptr() as *mut _,
            BufferBytes: bytes.len() as u32,
        };
        let mut output = MINIDUMP_CALLBACK_OUTPUT::default();
        assert_eq!(
            unsafe { callback((&mut state as *mut Output).cast(), &input, &mut output) },
            FALSE
        );
        assert_eq!(state.error, 5);
        assert_eq!(state.file.metadata().unwrap().len(), 0);
        drop(state);
        std::fs::remove_file(path).unwrap();
    }
}
