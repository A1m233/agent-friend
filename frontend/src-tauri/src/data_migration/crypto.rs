use super::model::{CONTAINER_MAGIC, CONTAINER_VERSION};
use age::{secrecy::SecretString, Decryptor, Encryptor};
use bip39::{Language, Mnemonic};
use std::{
    fs::{self, File},
    io::{self, BufReader, BufWriter, Read, Write},
    path::Path,
};

pub fn generate_transfer_code() -> Result<String, String> {
    Mnemonic::generate_in(Language::English, 12)
        .map(|mnemonic| mnemonic.to_string())
        .map_err(|error| format!("migration_code_generation_failed: {error}"))
}

pub fn encrypt_with(
    output: &Path,
    transfer_code: &str,
    producer: impl FnOnce(&mut dyn Write) -> Result<(), String>,
) -> Result<(), String> {
    validate_transfer_code(transfer_code)?;
    let partial = output.with_extension("part");
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent).map_err(io_error("package_parent_create_failed"))?;
    }
    let result = (|| {
        let destination = File::create(&partial).map_err(io_error("package_create_failed"))?;
        let mut destination = BufWriter::new(destination);
        destination
            .write_all(CONTAINER_MAGIC)
            .and_then(|_| destination.write_all(&CONTAINER_VERSION.to_le_bytes()))
            .map_err(io_error("package_header_write_failed"))?;
        let encryptor =
            Encryptor::with_user_passphrase(SecretString::from(transfer_code.to_owned()));
        let mut encrypted = encryptor
            .wrap_output(destination)
            .map_err(|error| format!("package_encrypt_init_failed: {error}"))?;
        producer(&mut encrypted)?;
        let mut destination = encrypted
            .finish()
            .map_err(|error| format!("package_encrypt_finish_failed: {error}"))?;
        destination
            .flush()
            .map_err(io_error("package_flush_failed"))?;
        destination
            .get_ref()
            .sync_all()
            .map_err(io_error("package_sync_failed"))?;
        fs::rename(&partial, output).map_err(io_error("package_commit_failed"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&partial);
    }
    result
}

pub fn decrypt_with(
    input: &Path,
    transfer_code: &str,
    consumer: impl FnOnce(&mut dyn Read) -> Result<(), String>,
) -> Result<(), String> {
    validate_transfer_code(transfer_code)?;
    let mut source =
        BufReader::new(File::open(input).map_err(|error| format!("package_open_failed: {error}"))?);
    let mut magic = [0_u8; 8];
    source
        .read_exact(&mut magic)
        .map_err(io_error("package_header_read_failed"))?;
    if &magic != CONTAINER_MAGIC {
        return Err("package_format_unsupported: invalid magic".to_string());
    }
    let mut version = [0_u8; 4];
    source
        .read_exact(&mut version)
        .map_err(io_error("package_header_read_failed"))?;
    if u32::from_le_bytes(version) != CONTAINER_VERSION {
        return Err("package_version_unsupported".to_string());
    }
    let decryptor = Decryptor::new(source)
        .map_err(|_| "package_decryption_failed: invalid package or transfer code".to_string())?;
    if !decryptor.is_scrypt() {
        return Err("package_encryption_unsupported".to_string());
    }
    let identity = age::scrypt::Identity::new(SecretString::from(transfer_code.to_owned()));
    let mut decrypted = decryptor
        .decrypt(std::iter::once(&identity as &dyn age::Identity))
        .map_err(|_| "package_decryption_failed: invalid package or transfer code".to_string())?;
    consumer(&mut decrypted)
}

#[cfg(test)]
pub fn encrypt_file(input: &Path, output: &Path, transfer_code: &str) -> Result<(), String> {
    encrypt_with(output, transfer_code, |writer| {
        let mut source =
            BufReader::new(File::open(input).map_err(io_error("package_input_open_failed"))?);
        io::copy(&mut source, writer).map_err(io_error("package_encrypt_failed"))?;
        Ok(())
    })
}

#[cfg(test)]
pub fn decrypt_file(input: &Path, output: &Path, transfer_code: &str) -> Result<(), String> {
    decrypt_with(input, transfer_code, |reader| {
        let partial = output.with_extension("part");
        let result = (|| {
            let mut destination = BufWriter::new(
                File::create(&partial).map_err(io_error("package_staging_create_failed"))?,
            );
            io::copy(reader, &mut destination).map_err(io_error("package_decryption_failed"))?;
            destination
                .flush()
                .map_err(io_error("package_staging_flush_failed"))?;
            destination
                .get_ref()
                .sync_all()
                .map_err(io_error("package_staging_sync_failed"))?;
            fs::rename(&partial, output).map_err(io_error("package_staging_commit_failed"))?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&partial);
        }
        result
    })
}

fn validate_transfer_code(transfer_code: &str) -> Result<(), String> {
    if transfer_code.split_whitespace().count() != 12 {
        return Err("migration_code_invalid: expected 12 words".to_string());
    }
    Ok(())
}

fn io_error(code: &'static str) -> impl FnOnce(io::Error) -> String {
    move |error| format!("{code}: {error}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transfer_code_has_twelve_words_and_round_trips() {
        let code = generate_transfer_code().expect("generate transfer code");
        assert_eq!(code.split_whitespace().count(), 12);
        let root = std::env::temp_dir().join(format!(
            "agent-friend-migration-crypto-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&root).unwrap();
        let source = root.join("source");
        let encrypted = root.join("transfer.agentfriend-transfer");
        let decrypted = root.join("decrypted");
        fs::write(&source, b"private migration payload").unwrap();
        encrypt_file(&source, &encrypted, &code).expect("encrypt");
        assert!(!fs::read(&encrypted)
            .unwrap()
            .windows(b"private migration payload".len())
            .any(|window| window == b"private migration payload"));
        decrypt_file(&encrypted, &decrypted, &code).expect("decrypt");
        assert_eq!(fs::read(&decrypted).unwrap(), b"private migration payload");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn wrong_code_does_not_create_plaintext_output() {
        let code = generate_transfer_code().unwrap();
        let wrong = generate_transfer_code().unwrap();
        let root = std::env::temp_dir().join(format!(
            "agent-friend-migration-wrong-code-{}",
            rand::random::<u64>()
        ));
        fs::create_dir_all(&root).unwrap();
        let source = root.join("source");
        let encrypted = root.join("transfer.agentfriend-transfer");
        let output = root.join("output");
        fs::write(&source, b"secret").unwrap();
        encrypt_file(&source, &encrypted, &code).unwrap();
        assert!(decrypt_file(&encrypted, &output, &wrong).is_err());
        assert!(!output.exists());
        assert!(!output.with_extension("part").exists());
        fs::remove_dir_all(root).unwrap();
    }
}
