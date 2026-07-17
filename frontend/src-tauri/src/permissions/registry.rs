use super::model::PermissionId;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum StartupPromptPolicy {
    Proactive,
    #[allow(dead_code)]
    OnDemandOnly,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct PermissionDefinition {
    pub id: PermissionId,
    pub revision: u32,
    pub enabled: bool,
    pub startup_policy: StartupPromptPolicy,
    pub title_key: &'static str,
    pub description_key: &'static str,
}

pub(crate) const MICROPHONE_DEFINITION: PermissionDefinition = PermissionDefinition {
    id: PermissionId::Microphone,
    revision: 1,
    enabled: true,
    startup_policy: StartupPromptPolicy::Proactive,
    title_key: "permissions.microphone.title",
    description_key: "permissions.microphone.description",
};

pub(crate) const PERMISSION_REGISTRY: &[PermissionDefinition] = &[MICROPHONE_DEFINITION];

pub(crate) fn enabled_permissions() -> impl Iterator<Item = &'static PermissionDefinition> {
    PERMISSION_REGISTRY
        .iter()
        .filter(|definition| definition.enabled)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_contains_only_the_enabled_microphone_definition() {
        assert_eq!(
            enabled_permissions().collect::<Vec<_>>(),
            [&MICROPHONE_DEFINITION]
        );
        assert_eq!(MICROPHONE_DEFINITION.revision, 1);
        assert_eq!(
            MICROPHONE_DEFINITION.startup_policy,
            StartupPromptPolicy::Proactive
        );
    }
}
