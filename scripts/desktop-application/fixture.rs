//! Owned test process. Writes only its own arguments, exits on the harness stop file.
use std::{env, fs, path::PathBuf, thread, time::{Duration, Instant}};
fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let root = PathBuf::from(&args[0]);
    fn quote(s: &str) -> String {
        format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
    }
    fs::write(root.join("started.json"), format!("{{\"pid\":{},\"arguments\":[{}]}}", std::process::id(),
        args[1..].iter().map(|s| quote(s)).collect::<Vec<_>>().join(","))).unwrap();
    let deadline = Instant::now() + Duration::from_secs(60);
    while !root.join("stop").exists() && Instant::now() < deadline { thread::sleep(Duration::from_millis(100)); }
    fs::write(root.join("stopped"), b"stopped").unwrap();
}
