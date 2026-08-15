use crate::paths;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CapabilityFile {
    schema_version: u32,
    memory_inspector: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalCapabilities {
    pub memory_inspector: bool,
}

pub fn local_capabilities() -> LocalCapabilities {
    if cfg!(debug_assertions) {
        return LocalCapabilities {
            memory_inspector: true,
        };
    }
    let memory_inspector = paths::owner_capabilities_path()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .is_some_and(|raw| release_file_enables_memory_inspector(&raw));
    LocalCapabilities { memory_inspector }
}

fn release_file_enables_memory_inspector(raw: &str) -> bool {
    serde_json::from_str::<CapabilityFile>(raw)
        .is_ok_and(|file| file.schema_version == 1 && file.memory_inspector)
}

#[tauri::command]
pub fn get_local_capabilities() -> LocalCapabilities {
    local_capabilities()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn debug_build_enables_memory_inspector() {
        assert!(local_capabilities().memory_inspector);
    }

    #[test]
    fn release_capability_parser_is_strict_and_fail_closed() {
        assert!(release_file_enables_memory_inspector(
            r#"{"schemaVersion":1,"memoryInspector":true}"#
        ));
        assert!(!release_file_enables_memory_inspector(
            r#"{"schemaVersion":2,"memoryInspector":true}"#
        ));
        assert!(!release_file_enables_memory_inspector(
            r#"{"schemaVersion":1,"memoryInspector":true,"extra":1}"#
        ));
        assert!(!release_file_enables_memory_inspector("not-json"));
    }
}
