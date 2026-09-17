//! One-shot desktop launch broker. The target belongs to Explorer, never this helper.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

#[path = "../desktop_applications/mod.rs"]
mod desktop_applications;

fn main() {
    std::process::exit(desktop_applications::run());
}
