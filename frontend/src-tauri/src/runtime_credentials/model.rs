use crate::runtime_config::{RuntimeConfigEffect, RuntimeConfigGroup, RuntimeConfigItemStatus};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeCredentialValidationStatus {
    None,
    Valid,
    Invalid,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeCredentialOperationPhase {
    Idle,
    Staging,
    Validating,
    AwaitingConfirmation,
    Persisting,
    Restarting,
    Applied,
    ApplyBlocked,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCredentialOperation {
    pub operation_id: Option<String>,
    pub phase: RuntimeCredentialOperationPhase,
    pub safe_code: Option<String>,
    pub message: Option<String>,
    pub persisted: bool,
    pub applied: bool,
}

impl Default for RuntimeCredentialOperation {
    fn default() -> Self {
        Self {
            operation_id: None,
            phase: RuntimeCredentialOperationPhase::Idle,
            safe_code: None,
            message: None,
            persisted: false,
            applied: false,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCredentialGroupSnapshot {
    pub group: RuntimeConfigGroup,
    pub items: Vec<RuntimeConfigItemStatus>,
    pub configured: bool,
    pub partially_configured: bool,
    pub missing_required_fields: Vec<String>,
    pub validation: RuntimeCredentialValidationStatus,
    pub validation_checked_at: Option<String>,
    pub validation_error_code: Option<String>,
    pub credential_revision: Option<String>,
    pub effect: RuntimeConfigEffect,
    pub operation: RuntimeCredentialOperation,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCredentialSnapshot {
    pub groups: Vec<RuntimeCredentialGroupSnapshot>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeCredentialFieldAction {
    Set,
    Delete,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCredentialFieldMutation {
    pub name: String,
    pub action: RuntimeCredentialFieldAction,
    pub value: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApplyRuntimeCredentialGroupPayload {
    pub group: RuntimeConfigGroup,
    pub changes: Vec<RuntimeCredentialFieldMutation>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCredentialOperationPayload {
    pub operation_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RetryRuntimeCredentialApplyPayload {
    pub group: RuntimeConfigGroup,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeCredentialAuthFailurePayload {
    pub group: RuntimeConfigGroup,
    pub credential_revision: String,
}
