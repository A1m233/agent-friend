// Copyright 2026 agent-friend contributors
// SPDX-License-Identifier: Apache-2.0 OR MIT

//! Runs without WebViews or application data, once per isolated process.
#[cfg(windows)]
fn main() {
  use std::{
    hint::black_box,
    sync::{Arc, Barrier},
    thread,
  };
  use tauri_runtime::{Runtime, RuntimeHandle, RuntimeInitArgs};
  use tauri_runtime_wry::Wry;

  let scenario = std::env::args().nth(1).expect("scenario required");
  let mut runtime = Wry::<()>::new(RuntimeInitArgs::default()).unwrap();
  let handle = runtime.handle();
  match scenario.as_str() {
    "drop-before-run" => drop(runtime),
    "iteration" => {
      runtime.run_iteration(|_| {});
      runtime.run_iteration(|_| {});
      drop(runtime);
    }
    "run-return" => {
      let worker = handle.clone();
      let thread = thread::spawn(move || {
        assert!(!worker.available_monitors().is_empty());
        let _ = worker.primary_monitor();
        let _ = worker.monitor_from_point(0.0, 0.0);
        worker.run_on_main_thread(|| {}).unwrap();
        worker.request_exit(0).unwrap();
      });
      assert_eq!(runtime.run_return(|_| {}), 0);
      thread.join().unwrap();
    }
    "close-with-pending" => {
      let worker = handle.clone();
      let thread = thread::spawn(move || {
        worker.request_exit(0).unwrap();
        // Queued after exit: resolve or return unavailable, never wait forever.
        let _ = worker.available_monitors();
      });
      assert_eq!(runtime.run_return(|_| {}), 0);
      thread.join().unwrap();
    }
    "clone-stress" => {
      let start = Arc::new(Barrier::new(9));
      let workers: Vec<_> = (0..8)
        .map(|_| {
          let handle = handle.clone();
          let start = start.clone();
          thread::spawn(move || {
            start.wait();
            for _ in 0..100_000 {
              let copy = black_box(handle.clone());
              black_box(&copy);
              drop(copy);
            }
          })
        })
        .collect();
      start.wait();
      for worker in workers {
        worker.join().unwrap();
      }
      drop(runtime);
    }
    _ => panic!("unknown scenario"),
  }
  // Handles may outlive the event loop, including on another thread.
  thread::spawn(move || {
    assert!(matches!(
      handle.run_on_main_thread(|| panic!("revoked callback")),
      Err(tauri_runtime::Error::EventLoopClosed)
    ));
    assert!(handle.cursor_position().is_err());
    assert!(handle.primary_monitor().is_none());
    assert!(handle.available_monitors().is_empty());
    assert!(handle.display_handle().is_err());
    assert!(!format!("{handle:?}").is_empty());
    drop(handle.clone());
  })
  .join()
  .unwrap();
  println!("PASS {scenario}: owner lifecycle and post-close handles");
}

#[cfg(not(windows))]
fn main() {
  eprintln!("This native runtime regression is Windows-only");
  std::process::exit(2);
}
