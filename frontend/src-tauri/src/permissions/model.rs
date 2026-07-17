use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionId {
    Microphone,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SystemPermissionState {
    NotDetermined,
    Granted,
    Denied,
    Restricted,
    Unsupported,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionPresentationState {
    NotRequested,
    Granted,
    Denied,
    Revoked,
    Restricted,
    Unsupported,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionRecoveryAction {
    Request,
    RetryRequest,
    OpenSystemSettings,
    None,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct SystemPermissionObservation {
    pub state: SystemPermissionState,
    pub recovery_action: PermissionRecoveryAction,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionItemSnapshot {
    pub id: PermissionId,
    pub definition_revision: u32,
    pub system_state: SystemPermissionState,
    pub presentation_state: PermissionPresentationState,
    pub recovery_action: PermissionRecoveryAction,
    pub handled_for_current_revision: bool,
    pub deferred_for_current_revision: bool,
    pub changed_at_ms: Option<u64>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionFlowSnapshot {
    pub flow_id: String,
    pub phase: PermissionFlowPhase,
    pub permission_ids: Vec<PermissionId>,
    pub current_permission_id: Option<PermissionId>,
    pub joined_request_ids: Vec<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionFlowPhase {
    AwaitingUser,
    Requesting,
    Refreshing,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionFlowReason {
    Startup,
    Upgrade,
    Revoked,
    FeatureFallback,
    Manual,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionFlowRequest {
    pub request_id: String,
    pub reason: PermissionFlowReason,
    pub permission_ids: Vec<PermissionId>,
    pub source_window: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionFlowTicketStatus {
    Started,
    Joined,
    Queued,
    Noop,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionFlowTicket {
    pub request_id: String,
    pub flow_id: Option<String>,
    pub status: PermissionFlowTicketStatus,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionFlowFinished {
    pub flow_id: String,
    pub request_ids: Vec<String>,
    pub granted_permission_ids: Vec<PermissionId>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionSnapshot {
    pub snapshot_revision: u64,
    pub refreshing: bool,
    pub active_flow: Option<PermissionFlowSnapshot>,
    pub items: Vec<PermissionItemSnapshot>,
}

impl PermissionSnapshot {
    pub(crate) fn item(&self, id: PermissionId) -> Option<&PermissionItemSnapshot> {
        self.items.iter().find(|item| item.id == id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn permission_ids_are_stable_json_keys() {
        assert_eq!(
            serde_json::to_string(&PermissionId::Microphone).unwrap(),
            "\"microphone\""
        );
    }
}
