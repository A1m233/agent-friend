//! Bounded, local-only pet input facts; deliberately independent of business state.
mod export;
mod incidents;
pub(crate) mod integration;
mod journal;
mod manager;
mod model;

pub(crate) use export::{attach_current, export_failed, export_for_bundle, Export};
pub(crate) use manager::PetInputEvidence;

#[cfg(test)]
pub(crate) fn test_temp_dir() -> std::path::PathBuf {
    let root = std::env::temp_dir();
    // macOS may expose the temp root through /var, a symlink.
    #[cfg(unix)]
    let root = root.canonicalize().expect("physical temporary directory");
    root
}

#[cfg(test)]
mod tests;
