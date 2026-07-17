use super::{
    history::PermissionHistoryEntry,
    model::{
        PermissionItemSnapshot, PermissionPresentationState, PermissionRecoveryAction,
        SystemPermissionObservation, SystemPermissionState,
    },
    registry::PermissionDefinition,
};

pub(crate) fn observe_system_state(
    history: &mut PermissionHistoryEntry,
    state: SystemPermissionState,
    observed_at_ms: u64,
) {
    if state == SystemPermissionState::Granted {
        history.ever_granted = true;
    }
    if history.last_observed_system_state != Some(state) {
        history.last_observed_system_state = Some(state);
        history.last_observed_at_ms = Some(observed_at_ms);
    }
}

pub(crate) fn project_permission(
    definition: &PermissionDefinition,
    observation: &SystemPermissionObservation,
    history: &PermissionHistoryEntry,
) -> PermissionItemSnapshot {
    let presentation_state = match observation.state {
        SystemPermissionState::NotDetermined => PermissionPresentationState::NotRequested,
        SystemPermissionState::Granted => PermissionPresentationState::Granted,
        SystemPermissionState::Denied if history.ever_granted => {
            PermissionPresentationState::Revoked
        }
        SystemPermissionState::Denied => PermissionPresentationState::Denied,
        SystemPermissionState::Restricted => PermissionPresentationState::Restricted,
        SystemPermissionState::Unsupported => PermissionPresentationState::Unsupported,
        SystemPermissionState::Unknown => PermissionPresentationState::Unknown,
    };
    let recovery_action = match observation.state {
        SystemPermissionState::Granted | SystemPermissionState::Unsupported => {
            PermissionRecoveryAction::None
        }
        SystemPermissionState::NotDetermined
            if observation.recovery_action == PermissionRecoveryAction::None =>
        {
            PermissionRecoveryAction::Request
        }
        _ => observation.recovery_action,
    };

    PermissionItemSnapshot {
        id: definition.id,
        definition_revision: definition.revision,
        system_state: observation.state,
        presentation_state,
        recovery_action,
        handled_for_current_revision: history.handled_revision == definition.revision,
        deferred_for_current_revision: history.deferred_revision == Some(definition.revision),
        changed_at_ms: history.last_observed_at_ms,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::permissions::{model::PermissionId, registry::MICROPHONE_DEFINITION};

    fn observation(
        state: SystemPermissionState,
        recovery_action: PermissionRecoveryAction,
    ) -> SystemPermissionObservation {
        SystemPermissionObservation {
            state,
            recovery_action,
        }
    }

    #[test]
    fn distinguishes_denial_from_revocation() {
        let denied = project_permission(
            &MICROPHONE_DEFINITION,
            &observation(
                SystemPermissionState::Denied,
                PermissionRecoveryAction::RetryRequest,
            ),
            &PermissionHistoryEntry::default(),
        );
        assert_eq!(
            denied.presentation_state,
            PermissionPresentationState::Denied
        );

        let revoked = project_permission(
            &MICROPHONE_DEFINITION,
            &observation(
                SystemPermissionState::Denied,
                PermissionRecoveryAction::OpenSystemSettings,
            ),
            &PermissionHistoryEntry {
                ever_granted: true,
                ..PermissionHistoryEntry::default()
            },
        );
        assert_eq!(
            revoked.presentation_state,
            PermissionPresentationState::Revoked
        );
    }

    #[test]
    fn handled_and_deferred_apply_only_to_the_current_definition_revision() {
        let current = PermissionHistoryEntry {
            handled_revision: 1,
            deferred_revision: Some(1),
            ..PermissionHistoryEntry::default()
        };
        let snapshot = project_permission(
            &MICROPHONE_DEFINITION,
            &observation(
                SystemPermissionState::NotDetermined,
                PermissionRecoveryAction::None,
            ),
            &current,
        );
        assert!(snapshot.handled_for_current_revision);
        assert!(snapshot.deferred_for_current_revision);
        assert_eq!(snapshot.recovery_action, PermissionRecoveryAction::Request);

        let next_definition = PermissionDefinition {
            id: PermissionId::Microphone,
            revision: 2,
            ..MICROPHONE_DEFINITION
        };
        let next = project_permission(
            &next_definition,
            &observation(
                SystemPermissionState::NotDetermined,
                PermissionRecoveryAction::Request,
            ),
            &current,
        );
        assert!(!next.handled_for_current_revision);
        assert!(!next.deferred_for_current_revision);
    }

    #[test]
    fn observing_granted_is_monotonic_and_changes_timestamp_only_on_state_change() {
        let mut history = PermissionHistoryEntry::default();
        observe_system_state(&mut history, SystemPermissionState::Granted, 10);
        observe_system_state(&mut history, SystemPermissionState::Granted, 20);
        assert!(history.ever_granted);
        assert_eq!(history.last_observed_at_ms, Some(10));

        observe_system_state(&mut history, SystemPermissionState::Denied, 30);
        assert!(history.ever_granted);
        assert_eq!(history.last_observed_at_ms, Some(30));
    }

    #[test]
    fn unsupported_and_unknown_never_look_granted() {
        for (state, expected) in [
            (
                SystemPermissionState::Restricted,
                PermissionPresentationState::Restricted,
            ),
            (
                SystemPermissionState::Unsupported,
                PermissionPresentationState::Unsupported,
            ),
            (
                SystemPermissionState::Unknown,
                PermissionPresentationState::Unknown,
            ),
        ] {
            let snapshot = project_permission(
                &MICROPHONE_DEFINITION,
                &observation(state, PermissionRecoveryAction::None),
                &PermissionHistoryEntry::default(),
            );
            assert_eq!(snapshot.presentation_state, expected);
            assert_ne!(
                snapshot.presentation_state,
                PermissionPresentationState::Granted
            );
        }
    }
}
