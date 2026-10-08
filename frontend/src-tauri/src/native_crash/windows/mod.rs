mod capture;
mod dump;
mod ipc;
mod watcher;

pub(super) use capture::current_failures;
pub(super) use capture::current_handoff_failure;
pub(super) use capture::{orderly_exit, panic_observed, register_service, start};
pub(super) use ipc::{replace_file, validate_file_parent};

pub(super) fn run_internal_mode() -> Option<i32> {
    let args: Vec<_> = std::env::args_os().collect();
    let mode = args.get(1)?.to_str()?;
    if mode == "--af-native-watch-v1" || mode == "--af-native-dump-v1" {
        return Some(
            match ipc::Connection::from_arguments(&args, mode == "--af-native-watch-v1") {
                Ok(connection) => {
                    if mode == "--af-native-watch-v1" {
                        watcher::run(connection)
                    } else {
                        dump::run(connection)
                    }
                }
                Err(_) => 64,
            },
        );
    }
    #[cfg(debug_assertions)]
    if mode == "--af-native-fixture-v1" {
        return Some(match (args.get(2).and_then(|s| s.to_str()), args.get(3)) {
            (Some(kind), Some(root)) => fixture_main(kind, std::path::Path::new(root)),
            _ => 64,
        });
    }
    // Release builds must reject fixture switches, never fall through and launch the desktop.
    if mode.starts_with("--af-native-") {
        return Some(64);
    }
    None
}

/// Deliberately only available in debug builds and restricted to an explicit isolated temp root.
#[cfg(debug_assertions)]
pub(super) fn fixture_main(mode: &str, root: &std::path::Path) -> i32 {
    if !cfg!(debug_assertions) {
        return 64;
    }
    let Ok(root) = root.canonicalize() else {
        return 64;
    };
    let Ok(temp) = std::env::temp_dir().canonicalize() else {
        return 64;
    };
    if !root.starts_with(&temp)
        || !root
            .file_name()
            .and_then(|s| s.to_str())
            .is_some_and(|s| s.starts_with("af-native-smoke-"))
    {
        return 64;
    }
    std::env::set_var("AGENT_FRIEND_LOG_DIR", &root);
    // A smoke fixture must not wait for an interactive Windows crash-report dialog.
    unsafe {
        use windows::Win32::System::Diagnostics::Debug::{
            SetErrorMode, SEM_FAILCRITICALERRORS, SEM_NOGPFAULTERRORBOX,
        };
        SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    }
    if mode == "service-child" {
        std::thread::sleep(std::time::Duration::from_millis(650));
        return 7;
    }
    if mode == "storage-unwritable" {
        let _ = std::fs::write(
            root.join("native-crashes"),
            b"isolated unwritable-directory fixture",
        );
    }
    let flags = match mode {
        "watcher-unavailable" => 1,
        "worker-timeout" => 2,
        "worker-failure" => 4,
        "watcher-delay" => 8,
        _ => 0,
    };
    capture::start_fixture(
        &format!(
            "fixture-{}-{:016x}",
            std::process::id(),
            rand::random::<u64>()
        ),
        flags,
    );
    std::panic::set_hook(Box::new(|_| panic_observed()));
    std::fs::write(root.join("fixture-ready"), std::process::id().to_string()).ok();
    match mode {
        "normal" => orderly_exit("normal-return"),
        "watcher-unavailable" | "storage-unwritable" => orderly_exit("normal-return"),
        "watcher-early-death" => {
            capture::fixture_kill_watcher();
            orderly_exit("normal-return");
        }
        "both-killed" => {
            capture::fixture_kill_watcher();
            unsafe {
                let _ = windows::Win32::System::Threading::TerminateProcess(
                    windows::Win32::System::Threading::GetCurrentProcess(),
                    23,
                );
            }
        }
        "worker-timeout" | "worker-failure" => {
            let _ = std::panic::catch_unwind(|| panic!("isolated worker failure fixture"));
            orderly_exit("normal-return");
        }
        "watcher-delay" => {
            let first = std::time::Instant::now();
            let _ = std::panic::catch_unwind(|| panic!("isolated stalled observer fixture"));
            let first_millis = first.elapsed().as_millis();
            let before = capture::fixture_sequence();
            let early = crate::native_crash::export_for_bundle(false);
            let _ = std::fs::write(
                root.join("fixture-early-coverage.json"),
                serde_json::to_vec(&early.coverage).unwrap(),
            );
            let second = std::time::Instant::now();
            let _ = std::panic::catch_unwind(|| panic!("disabled channel must not reuse payload"));
            let second_millis = second.elapsed().as_millis();
            let after = capture::fixture_sequence();
            let _=std::fs::write(root.join("handoff-observation.json"),serde_json::to_vec(&serde_json::json!({"firstMillis":first_millis,"secondMillis":second_millis,"beforeSequence":before,"afterSequence":after})).unwrap());
            std::thread::sleep(std::time::Duration::from_millis(2200));
            orderly_exit("normal-return");
        }
        "service-first" | "shell-first" => {
            use std::os::windows::process::CommandExt;
            let mut child = match std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--af-native-fixture-v1", "service-child"])
                .arg(&root)
                .creation_flags(0x08000000)
                .spawn()
            {
                Ok(c) => c,
                Err(_) => return 69,
            };
            register_service(&child, "bridge", 7);
            if mode == "service-first" {
                let _ = child.wait();
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            orderly_exit("normal-return");
        }
        "panic-caught" => {
            let _ = std::panic::catch_unwind(|| panic!("isolated native-crash fixture"));
            orderly_exit("normal-return");
        }
        "panic" => panic!("isolated native-crash fixture"),
        "ud2" => {
            #[cfg(target_arch = "x86_64")]
            unsafe {
                std::arch::asm!("ud2", options(noreturn));
            }
            #[cfg(not(target_arch = "x86_64"))]
            return 77;
        }
        "av" => unsafe {
            #[cfg(target_arch = "x86_64")]
            std::arch::asm!("xor rax, rax", "mov byte ptr [rax], 1", out("rax") _, options(nostack));
            #[cfg(not(target_arch = "x86_64"))]
            windows::Win32::System::Diagnostics::Debug::RaiseException(0xc0000005, 1, None);
        },
        "fastfail" => std::process::abort(),
        "forcekill" => unsafe {
            let _ = windows::Win32::System::Threading::TerminateProcess(
                windows::Win32::System::Threading::GetCurrentProcess(),
                23,
            );
        },
        "stack-overflow" => {
            #[inline(never)]
            #[allow(unconditional_recursion)]
            fn overflow(n: usize) -> usize {
                let block = [n; 1024];
                std::hint::black_box(&block);
                overflow(n.wrapping_add(1)) + block[0]
            }
            std::hint::black_box(overflow(1));
        }
        _ => return 64,
    }
    let export = crate::native_crash::export_for_bundle(false);
    if let Ok(bytes) = serde_json::to_vec(&export.coverage) {
        let _ = std::fs::write(root.join("fixture-coverage.json"), bytes);
    }
    0
}
