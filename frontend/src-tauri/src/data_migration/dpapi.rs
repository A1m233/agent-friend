#[cfg(target_os = "windows")]
pub fn protect_current_user(plaintext: &[u8]) -> Result<Vec<u8>, String> {
    use windows::core::w;
    use windows::Win32::{
        Foundation::{LocalFree, HLOCAL},
        Security::Cryptography::{CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB},
    };
    let input = CRYPT_INTEGER_BLOB {
        cbData: plaintext
            .len()
            .try_into()
            .map_err(|_| "backup_dpapi_input_too_large".to_string())?,
        pbData: plaintext.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptProtectData(
            &input,
            w!("agent-friend migration rollback key"),
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
        .map_err(|error| format!("backup_dpapi_protect_failed: {error}"))?;
        let bytes = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        let _ = LocalFree(Some(HLOCAL(output.pbData as *mut core::ffi::c_void)));
        Ok(bytes)
    }
}

#[cfg(target_os = "windows")]
pub fn unprotect_current_user(ciphertext: &[u8]) -> Result<Vec<u8>, String> {
    use windows::Win32::{
        Foundation::{LocalFree, HLOCAL},
        Security::Cryptography::{
            CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
        },
    };
    let input = CRYPT_INTEGER_BLOB {
        cbData: ciphertext
            .len()
            .try_into()
            .map_err(|_| "backup_dpapi_input_too_large".to_string())?,
        pbData: ciphertext.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptUnprotectData(
            &input,
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
        .map_err(|error| format!("backup_dpapi_unprotect_failed: {error}"))?;
        let bytes = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        let _ = LocalFree(Some(HLOCAL(output.pbData as *mut core::ffi::c_void)));
        Ok(bytes)
    }
}

#[cfg(not(target_os = "windows"))]
pub fn protect_current_user(_plaintext: &[u8]) -> Result<Vec<u8>, String> {
    Err("backup_dpapi_unsupported_platform".to_string())
}

#[cfg(not(target_os = "windows"))]
pub fn unprotect_current_user(_ciphertext: &[u8]) -> Result<Vec<u8>, String> {
    Err("backup_dpapi_unsupported_platform".to_string())
}

#[cfg(all(test, target_os = "windows"))]
mod tests {
    use super::*;

    #[test]
    fn dpapi_round_trip_for_current_user() {
        let protected = protect_current_user(b"rollback-secret").unwrap();
        assert_ne!(protected, b"rollback-secret");
        assert_eq!(
            unprotect_current_user(&protected).unwrap(),
            b"rollback-secret"
        );
    }
}
