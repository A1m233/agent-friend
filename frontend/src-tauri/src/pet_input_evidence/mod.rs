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
mod tests;
