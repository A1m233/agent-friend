// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

const DESKTOP_SHELL_ROLE_MARKER: &str = "agent-friend-executable-role:desktop-shell-v1";

fn main() {
    if let Some(code) = app_lib::native_crash::run_internal_mode() {
        std::process::exit(code);
    }
    // Keep a deterministic product-role marker in the final PE. The package
    // harness verifies it before publishing so a sibling Cargo binary (such as
    // the installer helper) can never be mistaken for the desktop shell again.
    std::hint::black_box(DESKTOP_SHELL_ROLE_MARKER);
    app_lib::run();
    app_lib::native_crash::desktop_returned();
}
