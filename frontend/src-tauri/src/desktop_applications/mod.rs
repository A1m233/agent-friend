use serde::Deserialize;
use serde_json::json;
use std::io::{Read, Write};
use std::path::Path;

#[cfg(windows)]
mod windows;

const PROTOCOL_VERSION: u32 = 1;
const MAX_REQUEST_BYTES: u64 = 65_536;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct LaunchRequest {
    protocol_version: u32,
    executable_path: String,
    arguments: Vec<String>,
    working_directory: String,
}

fn validate(request: &LaunchRequest) -> Result<(), &'static str> {
    let path = Path::new(&request.executable_path);
    if request.protocol_version != PROTOCOL_VERSION {
        return Err("protocol_mismatch");
    }
    if !path.is_absolute()
        || !path.is_file()
        || !path
            .extension()
            .is_some_and(|v| v.eq_ignore_ascii_case("exe"))
        || request.executable_path.starts_with(r"\\")
        || request.executable_path.contains('\0')
    {
        return Err("invalid_executable");
    }
    let workdir = Path::new(&request.working_directory);
    if !workdir.is_absolute() || !workdir.is_dir() || request.working_directory.contains('\0') {
        return Err("invalid_working_directory");
    }
    if request.arguments.len() > 128
        || request.arguments.iter().any(|v| v.contains('\0'))
        || quote_arguments(&request.arguments).encode_utf16().count() > 30_000
    {
        return Err("invalid_arguments");
    }
    Ok(())
}

// Windows argv quoting, not shell escaping. Shell metacharacters stay literal.
fn quote_arguments(arguments: &[String]) -> String {
    arguments
        .iter()
        .map(|argument| {
            let mut value = String::from("\"");
            let mut slashes = 0;
            for c in argument.chars() {
                if c == '\\' {
                    slashes += 1;
                } else {
                    value.extend(
                        std::iter::repeat('\\').take(slashes * if c == '"' { 2 } else { 1 }),
                    );
                    if c == '"' {
                        value.push('\\');
                    }
                    value.push(c);
                    slashes = 0;
                }
            }
            value.extend(std::iter::repeat('\\').take(slashes * 2));
            value.push('"');
            value
        })
        .collect::<Vec<_>>()
        .join(" ")
}

pub fn run() -> i32 {
    let mode = std::env::args().nth(1);
    if mode.as_deref() == Some("--identity") {
        println!(
            "{}",
            json!({"protocol_version": PROTOCOL_VERSION,
            "product": "agent-friend-app-launcher", "version": env!("CARGO_PKG_VERSION"),
            "build_identity": option_env!("AGENT_FRIEND_BUILD_IDENTITY_JSON").unwrap_or("")})
        );
        return 0;
    }
    if mode.is_some() {
        println!(
            "{}",
            json!({"status":"failed","error_code":"invalid_arguments"})
        );
        return 2;
    }
    let mut bytes = Vec::new();
    let request = std::io::stdin()
        .take(MAX_REQUEST_BYTES + 1)
        .read_to_end(&mut bytes)
        .ok()
        .filter(|_| bytes.len() <= MAX_REQUEST_BYTES as usize)
        .and_then(|_| serde_json::from_slice::<LaunchRequest>(&bytes).ok());
    let Some(request) = request else {
        println!(
            "{}",
            json!({"status":"failed","error_code":"invalid_request"})
        );
        return 2;
    };
    if let Err(code) = validate(&request) {
        println!("{}", json!({"status":"failed","error_code":code}));
        return 2;
    }
    #[cfg(windows)]
    let result = windows::launch(&request);
    #[cfg(not(windows))]
    let result: Result<(), (&str, Option<i32>)> = Err(("unsupported_platform", None));
    match result {
        Ok(()) => {
            println!(
                "{}",
                json!({"status":"accepted","lifecycle":"user_desktop",
                "readiness":"not_verified","protocol_version":PROTOCOL_VERSION})
            );
            0
        }
        Err((code, native_code)) => {
            // A COM failure after dispatch can lose the reply after Explorer acted.
            let status = if code == "shell_dispatch_failed" {
                "unknown"
            } else {
                "failed"
            };
            println!(
                "{}",
                json!({"status":status,"error_code":code,"native_code":native_code})
            );
            2
        }
    }
}

pub(super) fn dispatching() -> Result<(), (&'static str, Option<i32>)> {
    println!("{}", json!({"status":"dispatching"}));
    std::io::stdout()
        .flush()
        .map_err(|_| ("output_unavailable", None))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quotes_windows_arguments_without_shell_interpretation() {
        assert_eq!(
            quote_arguments(&[
                "".into(),
                "a b".into(),
                "c\\".into(),
                "a\"b".into(),
                "&x".into()
            ]),
            "\"\" \"a b\" \"c\\\\\" \"a\\\"b\" \"&x\""
        );
    }
}
