use std::path::Path;

const SAFETY_MARGIN_BYTES: u64 = 512 * 1024 * 1024;

#[cfg(target_os = "windows")]
pub fn ensure_available_space(path: &Path, required_bytes: u64) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows::{core::PCWSTR, Win32::Storage::FileSystem::GetDiskFreeSpaceExW};

    let wide = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect::<Vec<_>>();
    let mut available = 0_u64;
    unsafe {
        GetDiskFreeSpaceExW(
            PCWSTR::from_raw(wide.as_ptr()),
            Some(&mut available),
            None,
            None,
        )
    }
    .map_err(|error| format!("migration_disk_space_query_failed: {error}"))?;
    let budget = required_bytes.saturating_add(SAFETY_MARGIN_BYTES);
    if available < budget {
        return Err(format!(
            "migration_disk_space_insufficient: required {budget} bytes, available {available} bytes"
        ));
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn ensure_available_space(_path: &Path, _required_bytes: u64) -> Result<(), String> {
    Err("migration_platform_unsupported: Windows is required".to_string())
}
