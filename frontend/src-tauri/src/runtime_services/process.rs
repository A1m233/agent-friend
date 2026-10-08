//! One service generation owns its process tree, independently of other services.
use std::{
    io,
    process::{Child, Command, ExitStatus},
    thread,
    time::{Duration, Instant},
};

#[cfg(target_os = "windows")]
pub(super) struct ProcessJob {
    handle: windows::Win32::Foundation::HANDLE,
    assigned_count: std::sync::atomic::AtomicUsize,
}

// A Job Object handle is a process-wide kernel handle. Assigning child process handles and
// closing it are thread-safe Win32 operations; ownership remains unique in this wrapper.
#[cfg(target_os = "windows")]
unsafe impl Send for ProcessJob {}
#[cfg(target_os = "windows")]
unsafe impl Sync for ProcessJob {}

#[cfg(target_os = "windows")]
impl ProcessJob {
    pub(super) fn new() -> Result<Self, String> {
        use windows::{
            core::PCWSTR,
            Win32::System::JobObjects::{
                CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
                JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            },
        };
        let handle =
            unsafe { CreateJobObjectW(None, PCWSTR::null()) }.map_err(|error| error.to_string())?;
        let mut information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if let Err(error) = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                std::ptr::addr_of!(information).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } {
            unsafe {
                let _ = windows::Win32::Foundation::CloseHandle(handle);
            }
            return Err(error.to_string());
        }
        Ok(Self {
            handle,
            assigned_count: std::sync::atomic::AtomicUsize::new(0),
        })
    }

    pub(super) fn assign(&self, child: &Child) -> Result<(), String> {
        use std::os::windows::io::AsRawHandle;
        use windows::Win32::{Foundation::HANDLE, System::JobObjects::AssignProcessToJobObject};
        let process = HANDLE(child.as_raw_handle());
        unsafe { AssignProcessToJobObject(self.handle, process) }
            .map_err(|error| error.to_string())?;
        self.assigned_count
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        Ok(())
    }

    pub(super) fn assigned_count(&self) -> usize {
        self.assigned_count
            .load(std::sync::atomic::Ordering::Relaxed)
    }
}

#[cfg(target_os = "windows")]
impl Drop for ProcessJob {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.handle);
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub(super) struct ProcessJob;

#[cfg(not(target_os = "windows"))]
impl ProcessJob {
    pub(super) fn new() -> Result<Self, String> {
        Ok(Self)
    }

    pub(super) fn assign(&self, _child: &Child) -> Result<(), String> {
        Ok(())
    }

    pub(super) fn assigned_count(&self) -> usize {
        0
    }
}

pub(super) struct ServiceProcess {
    child: Child,
    #[cfg(windows)]
    job: ProcessJob,
}

impl ServiceProcess {
    pub(super) fn register_crash_observer(&self, role: &str, generation: u64) {
        #[cfg(windows)]
        crate::native_crash::register_service(&self.child, role, generation);
        #[cfg(not(windows))]
        let _ = (role, generation);
    }

    pub(super) fn spawn(
        command: &mut Command,
        parent_job: Option<&ProcessJob>,
    ) -> Result<Self, String> {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // Bind before executing any user code: launchers cannot spawn outside our job.
            command.creation_flags(0x08000000 | 0x00000004); // NO_WINDOW | SUSPENDED
            let job = ProcessJob::new()?;
            let mut child = command.spawn().map_err(|e| e.to_string())?;
            let configure = (|| {
                if let Some(parent) = parent_job {
                    parent.assign(&child)?;
                }
                job.assign(&child)?;
                resume_initial_thread(child.id())
            })();
            if let Err(error) = configure {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
            Ok(Self { child, job })
        }
        #[cfg(not(windows))]
        {
            let _ = parent_job;
            Ok(Self {
                child: command.spawn().map_err(|e| e.to_string())?,
            })
        }
    }

    pub(super) fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        let root = self.child.try_wait()?;
        #[cfg(windows)]
        if self.job.active_processes()? != 0 {
            return Ok(None);
        }
        Ok(root)
    }

    pub(super) fn terminate(&mut self) -> io::Result<()> {
        #[cfg(windows)]
        {
            let members = self.job.members()?;
            self.job.terminate()?;
            let deadline = Instant::now() + Duration::from_secs(5);
            for member in members {
                use windows::Win32::{
                    Foundation::WAIT_OBJECT_0, System::Threading::WaitForSingleObject,
                };
                if unsafe {
                    WaitForSingleObject(
                        member.0,
                        deadline
                            .saturating_duration_since(Instant::now())
                            .as_millis()
                            .min(u32::MAX as u128) as u32,
                    )
                } != WAIT_OBJECT_0
                {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "service member did not finish exiting",
                    ));
                }
            }
        }
        #[cfg(not(windows))]
        if self.child.try_wait()?.is_none() {
            self.child.kill()?;
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if self.try_wait()?.is_some() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "service process tree did not exit",
                ));
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for ServiceProcess {
    fn drop(&mut self) {
        if let Err(error) = self.terminate() {
            log::error!("service process tree cleanup failed: {error}");
        }
    }
}

#[cfg(windows)]
impl ProcessJob {
    fn members(&self) -> io::Result<Vec<ProcessHandle>> {
        use windows::Win32::{
            Foundation::{ERROR_INVALID_PARAMETER, ERROR_MORE_DATA},
            System::{
                JobObjects::{
                    IsProcessInJob, JobObjectBasicProcessIdList, QueryInformationJobObject,
                    JOBOBJECT_BASIC_PROCESS_ID_LIST,
                },
                Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE},
            },
        };
        let mut size = 32;
        loop {
            let mut buffer = vec![0usize; size];
            let info = buffer
                .as_mut_ptr()
                .cast::<JOBOBJECT_BASIC_PROCESS_ID_LIST>();
            let result = unsafe {
                QueryInformationJobObject(
                    Some(self.handle),
                    JobObjectBasicProcessIdList,
                    info.cast(),
                    (buffer.len() * std::mem::size_of::<usize>()) as u32,
                    None,
                )
            };
            match result {
                Err(error) if error.code() == ERROR_MORE_DATA.to_hresult() && size < 65536 => {
                    size *= 2;
                    continue;
                }
                Err(error) => return Err(io::Error::other(error)),
                Ok(()) => {}
            }
            let ids = unsafe {
                std::slice::from_raw_parts(
                    (*info).ProcessIdList.as_ptr(),
                    (*info).NumberOfProcessIdsInList as usize,
                )
            };
            let mut members = Vec::new();
            for pid in ids {
                let handle = match unsafe {
                    OpenProcess(
                        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                        false,
                        *pid as u32,
                    )
                } {
                    Ok(handle) => ProcessHandle(handle),
                    Err(error) if error.code() == ERROR_INVALID_PARAMETER.to_hresult() => continue,
                    Err(error) => return Err(io::Error::other(error)),
                };
                let mut belongs = windows::core::BOOL::default();
                unsafe { IsProcessInJob(handle.0, Some(self.handle), &mut belongs) }
                    .map_err(io::Error::other)?;
                if belongs.as_bool() {
                    members.push(handle);
                }
            }
            return Ok(members);
        }
    }

    fn active_processes(&self) -> io::Result<u32> {
        use windows::Win32::System::JobObjects::{
            JobObjectBasicAccountingInformation, QueryInformationJobObject,
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
        };
        let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        unsafe {
            QueryInformationJobObject(
                Some(self.handle),
                JobObjectBasicAccountingInformation,
                std::ptr::addr_of_mut!(info).cast(),
                std::mem::size_of_val(&info) as u32,
                None,
            )
        }
        .map_err(io::Error::other)?;
        Ok(info.ActiveProcesses)
    }

    fn terminate(&self) -> io::Result<()> {
        unsafe { windows::Win32::System::JobObjects::TerminateJobObject(self.handle, 1) }
            .map_err(io::Error::other)
    }
}

#[cfg(windows)]
struct ProcessHandle(windows::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl Drop for ProcessHandle {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

#[cfg(windows)]
fn resume_initial_thread(pid: u32) -> Result<(), String> {
    use windows::Win32::{
        Foundation::{CloseHandle, ERROR_NO_MORE_FILES},
        System::{
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD,
                THREADENTRY32,
            },
            Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
        },
    };
    // The process is suspended and cannot exit or create more threads before we resume it.
    let snapshot =
        unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) }.map_err(|e| e.to_string())?;
    let result = (|| {
        let mut entry = THREADENTRY32 {
            dwSize: std::mem::size_of::<THREADENTRY32>() as u32,
            ..Default::default()
        };
        unsafe { Thread32First(snapshot, &mut entry) }.map_err(|e| e.to_string())?;
        loop {
            if entry.th32OwnerProcessID == pid {
                let thread =
                    unsafe { OpenThread(THREAD_SUSPEND_RESUME, false, entry.th32ThreadID) }
                        .map_err(|e| e.to_string())?;
                let resumed = unsafe { ResumeThread(thread) };
                let error = (resumed == u32::MAX).then(windows::core::Error::from_win32);
                unsafe {
                    let _ = CloseHandle(thread);
                }
                return error.map_or(Ok(()), |e| Err(e.to_string()));
            }
            if let Err(error) = unsafe { Thread32Next(snapshot, &mut entry) } {
                if error.code() == ERROR_NO_MORE_FILES.to_hresult() {
                    break;
                }
                return Err(error.to_string());
            }
        }
        Err("suspended service main thread not found".into())
    })();
    unsafe {
        let _ = CloseHandle(snapshot);
    }
    result
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::{
        fs::{self, OpenOptions},
        os::windows::fs::OpenOptionsExt,
        path::{Path, PathBuf},
    };

    fn fixture_command(dir: &Path, mode: &str) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "runtime_services::process::tests::fixture_main",
                "--nocapture",
            ])
            .env("ISSUE065_FIXTURE_DIR", dir)
            .env("ISSUE065_FIXTURE_MODE", mode)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        command
    }

    fn wait_for(mut predicate: impl FnMut() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !predicate() {
            assert!(Instant::now() < deadline, "process fixture timed out");
            thread::sleep(Duration::from_millis(20));
        }
    }

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "agent-friend-065-{}-{}",
                std::process::id(),
                rand::random::<u64>()
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn locked(&self) -> bool {
            OpenOptions::new()
                .read(true)
                .write(true)
                .share_mode(0)
                .open(self.0.join("writer.lock"))
                .is_err()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn fixture_main() {
        let Some(dir) = std::env::var_os("ISSUE065_FIXTURE_DIR") else {
            return;
        };
        let dir = PathBuf::from(dir);
        match std::env::var("ISSUE065_FIXTURE_MODE").unwrap().as_str() {
            "root" | "root-exit" => {
                let mut middle = fixture_command(&dir, "middle").spawn().unwrap();
                wait_for(|| dir.join("ready").is_file());
                if std::env::var("ISSUE065_FIXTURE_MODE").unwrap() == "root" {
                    let _ = middle.wait();
                }
            }
            "middle" => {
                let _ = fixture_command(&dir, "worker").spawn().unwrap().wait();
            }
            "worker" => {
                let _lock = OpenOptions::new()
                    .create(true)
                    .truncate(false)
                    .read(true)
                    .write(true)
                    .share_mode(0)
                    .open(dir.join("writer.lock"))
                    .unwrap();
                fs::write(dir.join("ready"), std::process::id().to_string()).unwrap();
                thread::sleep(Duration::from_secs(60));
            }
            _ => panic!("unknown fixture mode"),
        }
    }

    #[test]
    fn forced_stop_reaps_descendants_releases_lock_and_allows_next_generation() {
        let fixture = Fixture::new();
        let unrelated = Fixture::new();
        let mut voice =
            ServiceProcess::spawn(&mut fixture_command(&unrelated.0, "root"), None).unwrap();
        wait_for(|| unrelated.0.join("ready").is_file());
        for _generation in 0..2 {
            let mut bridge =
                ServiceProcess::spawn(&mut fixture_command(&fixture.0, "root"), None).unwrap();
            wait_for(|| fixture.0.join("ready").is_file());
            assert!(fixture.locked());
            assert!(bridge.job.active_processes().unwrap() >= 3);
            bridge.terminate().unwrap();
            assert!(bridge.try_wait().unwrap().is_some());
            assert_eq!(bridge.job.active_processes().unwrap(), 0);
            assert!(!fixture.locked());
            assert!(
                voice.try_wait().unwrap().is_none(),
                "stopping Bridge must not stop Voice"
            );
            fs::remove_file(fixture.0.join("ready")).unwrap();
        }
        voice.terminate().unwrap();
        assert!(!unrelated.locked());
    }

    #[test]
    fn launcher_exit_is_not_tree_exit_and_drop_releases_descendant_lock() {
        let fixture = Fixture::new();
        let parent = ProcessJob::new().unwrap();
        let mut process =
            ServiceProcess::spawn(&mut fixture_command(&fixture.0, "root-exit"), Some(&parent))
                .unwrap();
        wait_for(|| process.child.try_wait().unwrap().is_some());
        assert!(fixture.locked());
        assert!(process.try_wait().unwrap().is_none());
        drop(process);
        let released = OpenOptions::new()
            .read(true)
            .write(true)
            .share_mode(0)
            .open(fixture.0.join("writer.lock"));
        assert!(
            released.is_ok(),
            "writer lock after tree drop: {:?}, parent active={:?}",
            released.err(),
            parent.active_processes()
        );
        assert_eq!(parent.active_processes().unwrap(), 0);
    }

    #[test]
    fn terminate_failure_is_returned_and_process_remains_owned_for_retry() {
        let fixture = Fixture::new();
        let mut process =
            ServiceProcess::spawn(&mut fixture_command(&fixture.0, "root"), None).unwrap();
        wait_for(|| fixture.0.join("ready").is_file());
        let handle = std::mem::replace(
            &mut process.job.handle,
            windows::Win32::Foundation::HANDLE::default(),
        );
        let error = process.terminate();
        process.job.handle = handle;
        assert!(error.is_err());
        assert!(process.try_wait().unwrap().is_none());
        assert!(fixture.locked());
        process.terminate().unwrap();
        assert!(!fixture.locked());
    }
}
