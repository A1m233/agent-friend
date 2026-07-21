use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum BackgroundOnlinePolicy {
    #[default]
    FollowSystem,
    PluggedIn,
    Always,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum PowerDemandReason {
    AgentTask,
    ImOnline,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum PowerSource {
    Ac,
    Battery,
    #[default]
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum PlatformCapability {
    Supported,
    Unsupported,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum InhibitorState {
    #[default]
    Inactive,
    Active,
    Error,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum BridgeDemandState {
    Available,
    #[default]
    Unavailable,
}

#[allow(dead_code)]
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum DesktopPlatform {
    Windows,
    Macos,
    Linux,
    Other,
}

impl DesktopPlatform {
    pub fn current() -> Self {
        #[cfg(target_os = "windows")]
        return Self::Windows;
        #[cfg(target_os = "macos")]
        return Self::Macos;
        #[cfg(target_os = "linux")]
        return Self::Linux;
        #[allow(unreachable_code)]
        Self::Other
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PowerPolicyError {
    pub code: String,
    pub message: String,
}

impl PowerPolicyError {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PowerPolicySnapshot {
    pub platform: DesktopPlatform,
    pub policy: BackgroundOnlinePolicy,
    pub power_source: PowerSource,
    pub reasons: BTreeSet<PowerDemandReason>,
    pub capability: PlatformCapability,
    pub inhibitor_state: InhibitorState,
    pub bridge_state: BridgeDemandState,
    pub last_error: Option<PowerPolicyError>,
}

impl PowerPolicySnapshot {
    pub fn initial(policy: BackgroundOnlinePolicy, capability: PlatformCapability) -> Self {
        Self {
            platform: DesktopPlatform::current(),
            policy,
            power_source: PowerSource::Unknown,
            reasons: BTreeSet::new(),
            capability,
            inhibitor_state: InhibitorState::Inactive,
            bridge_state: BridgeDemandState::Unavailable,
            last_error: None,
        }
    }
}
