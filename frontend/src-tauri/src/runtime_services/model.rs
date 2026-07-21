use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeServiceKind {
    Bridge,
    Voice,
}

impl RuntimeServiceKind {
    pub fn command(self) -> &'static str {
        match self {
            Self::Bridge => "bridge",
            Self::Voice => "voice",
        }
    }

    pub fn module(self) -> &'static str {
        match self {
            Self::Bridge => "agent_bridge",
            Self::Voice => "voice_bridge",
        }
    }

    pub fn base_url(self) -> &'static str {
        match self {
            Self::Bridge => "http://127.0.0.1:18800",
            Self::Voice => "http://127.0.0.1:18900",
        }
    }

    pub fn port(self) -> u16 {
        match self {
            Self::Bridge => 18800,
            Self::Voice => 18900,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeServicePhase {
    Stopped,
    Starting,
    Ready,
    Quiescing,
    Stopping,
    Blocked,
    Unavailable,
    Failed,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeServiceError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeServiceSnapshot {
    pub service: RuntimeServiceKind,
    pub phase: RuntimeServicePhase,
    pub generation: u64,
    pub operation_id: Option<String>,
    pub error: Option<RuntimeServiceError>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AgentTaskDemand {
    pub active: bool,
    pub queued: usize,
    pub running: usize,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ImOnlineDemand {
    pub active: bool,
    pub binding_count: usize,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct BackgroundDemandSnapshot {
    pub agent_task: AgentTaskDemand,
    pub im_online: ImOnlineDemand,
}

impl RuntimeServiceSnapshot {
    pub fn stopped(service: RuntimeServiceKind) -> Self {
        Self {
            service,
            phase: RuntimeServicePhase::Stopped,
            generation: 0,
            operation_id: None,
            error: None,
        }
    }
}
