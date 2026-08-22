use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeSet;

const CONTRACT_SOURCE: &str = include_str!("bundle-contract-v1.json");

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct BundleContract {
    format_version: u32,
    schema_revision: u32,
    core: ContractSurface,
    capabilities: Vec<CapabilityContract>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ContractSurface {
    manifest_fields: Vec<String>,
    required_entries: Vec<String>,
    entry_patterns: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CapabilityContract {
    id: String,
    manifest_fields: Vec<String>,
    required_entries: Vec<String>,
    entry_patterns: Vec<String>,
}

impl BundleContract {
    pub(super) fn load() -> Result<Self, String> {
        let contract: Self = serde_json::from_str(CONTRACT_SOURCE)
            .map_err(|error| format!("parse diagnostic bundle contract failed: {error}"))?;
        contract.validate_definition()?;
        Ok(contract)
    }

    pub(super) fn format_version(&self) -> u32 {
        self.format_version
    }

    pub(super) fn schema_revision(&self) -> u32 {
        self.schema_revision
    }

    pub(super) fn capability_ids(&self) -> Vec<&str> {
        self.capabilities
            .iter()
            .map(|capability| capability.id.as_str())
            .collect()
    }

    pub(super) fn validate_bundle<'a>(
        &self,
        manifest: &Value,
        entry_names: impl IntoIterator<Item = &'a str>,
    ) -> Result<(), String> {
        let manifest = manifest
            .as_object()
            .ok_or_else(|| "diagnostic bundle manifest is not an object".to_string())?;
        let actual_fields = manifest.keys().map(String::as_str).collect::<BTreeSet<_>>();
        let expected_fields = self.manifest_fields();
        if actual_fields != expected_fields {
            return Err(format!(
                "diagnostic bundle manifest fields do not match contract: expected {expected_fields:?}, got {actual_fields:?}"
            ));
        }
        if manifest.get("formatVersion").and_then(Value::as_u64) != Some(self.format_version as u64)
        {
            return Err("diagnostic bundle formatVersion does not match contract".into());
        }
        if manifest.get("schemaRevision").and_then(Value::as_u64)
            != Some(self.schema_revision as u64)
        {
            return Err("diagnostic bundle schemaRevision does not match contract".into());
        }
        let actual_capabilities = manifest
            .get("capabilities")
            .and_then(Value::as_array)
            .ok_or_else(|| "diagnostic bundle capabilities is not an array".to_string())?
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .ok_or_else(|| "diagnostic bundle capability is not a string".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        if actual_capabilities != self.capability_ids() {
            return Err(format!(
                "diagnostic bundle capabilities do not match contract: expected {:?}, got {actual_capabilities:?}",
                self.capability_ids()
            ));
        }

        let entry_names = entry_names.into_iter().collect::<BTreeSet<_>>();
        let patterns = self.entry_patterns();
        let unknown_entries = entry_names
            .iter()
            .filter(|name| {
                !patterns
                    .iter()
                    .any(|pattern| pattern_matches(pattern, name))
            })
            .copied()
            .collect::<Vec<_>>();
        if !unknown_entries.is_empty() {
            return Err(format!(
                "diagnostic bundle contains entries absent from contract: {unknown_entries:?}"
            ));
        }
        let missing_entries = self
            .required_entries()
            .into_iter()
            .filter(|name| !entry_names.contains(*name))
            .collect::<Vec<_>>();
        if !missing_entries.is_empty() {
            return Err(format!(
                "diagnostic bundle is missing contract-required entries: {missing_entries:?}"
            ));
        }
        Ok(())
    }

    fn validate_definition(&self) -> Result<(), String> {
        if self.format_version == 0 || self.schema_revision == 0 {
            return Err("diagnostic bundle contract versions must be positive".into());
        }
        let capability_ids = self.capability_ids();
        if capability_ids.iter().any(|id| id.is_empty())
            || capability_ids
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .len()
                != capability_ids.len()
        {
            return Err("diagnostic bundle capability ids must be non-empty and unique".into());
        }
        ensure_unique("manifest field", self.manifest_field_values())?;
        ensure_unique("entry pattern", self.entry_pattern_values())?;
        ensure_unique("required entry", self.required_entry_values())?;
        let patterns = self.entry_patterns();
        for required in self.required_entries() {
            if !patterns
                .iter()
                .any(|pattern| pattern_matches(pattern, &required))
            {
                return Err(format!(
                    "diagnostic bundle required entry is not covered by an entry pattern: {required}"
                ));
            }
        }
        Ok(())
    }

    fn manifest_fields(&self) -> BTreeSet<&str> {
        self.manifest_field_values().into_iter().collect()
    }

    fn manifest_field_values(&self) -> Vec<&str> {
        self.core
            .manifest_fields
            .iter()
            .chain(
                self.capabilities
                    .iter()
                    .flat_map(|capability| capability.manifest_fields.iter()),
            )
            .map(String::as_str)
            .collect()
    }

    fn required_entries(&self) -> Vec<&str> {
        self.required_entry_values()
    }

    fn required_entry_values(&self) -> Vec<&str> {
        self.core
            .required_entries
            .iter()
            .chain(
                self.capabilities
                    .iter()
                    .flat_map(|capability| capability.required_entries.iter()),
            )
            .map(String::as_str)
            .collect()
    }

    fn entry_patterns(&self) -> Vec<&str> {
        self.entry_pattern_values()
    }

    fn entry_pattern_values(&self) -> Vec<&str> {
        self.core
            .entry_patterns
            .iter()
            .chain(
                self.capabilities
                    .iter()
                    .flat_map(|capability| capability.entry_patterns.iter()),
            )
            .map(String::as_str)
            .collect()
    }
}

fn ensure_unique(kind: &str, values: Vec<&str>) -> Result<(), String> {
    if values.iter().copied().collect::<BTreeSet<_>>().len() != values.len() {
        return Err(format!("diagnostic bundle contract has duplicate {kind}s"));
    }
    Ok(())
}

fn pattern_matches(pattern: &str, value: &str) -> bool {
    let Some((prefix, suffix)) = pattern.split_once('*') else {
        return pattern == value;
    };
    !suffix.contains('*')
        && value.len() >= prefix.len() + suffix.len()
        && value.starts_with(prefix)
        && value.ends_with(suffix)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn representative_manifest(contract: &BundleContract) -> Value {
        json!({
            "formatVersion": contract.format_version(),
            "schemaRevision": contract.schema_revision(),
            "capabilities": contract.capability_ids(),
            "generatedAt": "2026-08-01T00:00:00+00:00",
            "outcome": "complete",
            "requestedSessionIds": [],
            "includedSessionIds": [],
            "failures": [],
            "excluded": [],
            "logLimits": {},
            "logs": [],
            "windowsShell": {},
            "attachments": {},
            "renderEvidence": {},
        })
    }

    #[test]
    fn current_contract_accepts_representative_bundle() {
        let contract = BundleContract::load().expect("load contract");
        let manifest = representative_manifest(&contract);
        contract
            .validate_bundle(
                &manifest,
                [
                    "manifest.json",
                    "diagnostics/application.json",
                    "diagnostics/environment.json",
                    "diagnostics/runtime-services.json",
                    "diagnostics/runtime-config.json",
                    "diagnostics/desktop-instance.json",
                    "diagnostics/execution-environments.json",
                    "install/integrity.json",
                    "diagnostics/webview2.json",
                    "diagnostics/webview2-render-evidence.json",
                    "diagnostics/attachments.json",
                    "diagnostics/windows-shell/events-001.jsonl",
                    "render-evidence/webview2-pet.png",
                    "render-evidence/windows-desktop.png",
                ],
            )
            .expect("validate representative bundle");
    }

    #[test]
    fn current_contract_rejects_undeclared_fields_and_entries() {
        let contract = BundleContract::load().expect("load contract");
        let mut manifest = representative_manifest(&contract);
        manifest
            .as_object_mut()
            .expect("manifest object")
            .insert("forgottenConsumerField".into(), Value::Null);
        let error = contract
            .validate_bundle(&manifest, ["unexpected/private.txt"])
            .expect_err("contract should reject drift");
        assert!(error.contains("manifest fields do not match contract"));

        let manifest = representative_manifest(&contract);
        let error = contract
            .validate_bundle(
                &manifest,
                [
                    "manifest.json",
                    "diagnostics/application.json",
                    "diagnostics/environment.json",
                    "diagnostics/runtime-services.json",
                    "diagnostics/runtime-config.json",
                    "diagnostics/desktop-instance.json",
                    "diagnostics/webview2.json",
                    "diagnostics/webview2-render-evidence.json",
                    "diagnostics/attachments.json",
                    "unexpected/private.txt",
                ],
            )
            .expect_err("contract should reject undeclared entry");
        assert!(error.contains("entries absent from contract"));
    }
}
