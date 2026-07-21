use super::model::{BackgroundOnlinePolicy, PlatformCapability, PowerSource};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum PolicyDecision {
    Inhibit,
    FollowSystem,
    WaitingForAc,
    PowerSourceUnknown,
    Unsupported,
}

pub(crate) fn evaluate(
    policy: BackgroundOnlinePolicy,
    power_source: PowerSource,
    has_reasons: bool,
    capability: PlatformCapability,
) -> PolicyDecision {
    if capability == PlatformCapability::Unsupported {
        return PolicyDecision::Unsupported;
    }
    if policy == BackgroundOnlinePolicy::FollowSystem || !has_reasons {
        return PolicyDecision::FollowSystem;
    }
    match policy {
        BackgroundOnlinePolicy::FollowSystem => PolicyDecision::FollowSystem,
        BackgroundOnlinePolicy::Always => PolicyDecision::Inhibit,
        BackgroundOnlinePolicy::PluggedIn => match power_source {
            PowerSource::Ac => PolicyDecision::Inhibit,
            PowerSource::Battery => PolicyDecision::WaitingForAc,
            PowerSource::Unknown => PolicyDecision::PowerSourceUnknown,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn policy_matrix_is_explicit() {
        use PowerSource::*;

        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::FollowSystem,
                Ac,
                true,
                PlatformCapability::Supported
            ),
            PolicyDecision::FollowSystem
        );
        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::PluggedIn,
                Ac,
                false,
                PlatformCapability::Supported
            ),
            PolicyDecision::FollowSystem
        );
        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::PluggedIn,
                Ac,
                true,
                PlatformCapability::Supported
            ),
            PolicyDecision::Inhibit
        );
        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::PluggedIn,
                Battery,
                true,
                PlatformCapability::Supported
            ),
            PolicyDecision::WaitingForAc
        );
        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::PluggedIn,
                Unknown,
                true,
                PlatformCapability::Supported
            ),
            PolicyDecision::PowerSourceUnknown
        );
        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::Always,
                Battery,
                true,
                PlatformCapability::Supported
            ),
            PolicyDecision::Inhibit
        );
        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::Always,
                Unknown,
                true,
                PlatformCapability::Supported
            ),
            PolicyDecision::Inhibit
        );
        assert_eq!(
            evaluate(
                BackgroundOnlinePolicy::Always,
                Ac,
                true,
                PlatformCapability::Unsupported
            ),
            PolicyDecision::Unsupported
        );
    }
}
