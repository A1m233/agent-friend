use super::model::{
    RuntimeServiceError, RuntimeServiceKind, RuntimeServicePhase, RuntimeServiceSnapshot,
};
use rand::{rngs::OsRng, RngCore};
use std::{
    collections::HashMap,
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Arc,
    time::Duration,
};
use tauri::{AppHandle, Emitter};
use tokio::sync::Mutex;

const EVENT_CHANGED: &str = "runtime-service://changed";
const READINESS_ATTEMPTS: usize = 50;
const POLL_DELAY: Duration = Duration::from_millis(200);

struct ServiceSlot {
    snapshot: RuntimeServiceSnapshot,
    child: Option<Child>,
    token: Option<String>,
}

impl ServiceSlot {
    fn new(service: RuntimeServiceKind) -> Self {
        Self {
            snapshot: RuntimeServiceSnapshot::stopped(service),
            child: None,
            token: None,
        }
    }

    fn begin(&mut self, phase: RuntimeServicePhase) -> u64 {
        self.snapshot.generation += 1;
        self.snapshot.phase = phase;
        self.snapshot.operation_id = Some(format!(
            "{}-{}",
            self.snapshot.service.module(),
            self.snapshot.generation
        ));
        self.snapshot.error = None;
        self.snapshot.generation
    }

    fn fail(&mut self, phase: RuntimeServicePhase, code: &str, message: String) {
        self.snapshot.phase = phase;
        self.snapshot.error = Some(RuntimeServiceError {
            code: code.to_string(),
            message,
            retryable: true,
        });
    }
}

pub struct RuntimeServiceSupervisor {
    slots: HashMap<RuntimeServiceKind, Arc<Mutex<ServiceSlot>>>,
    operations: HashMap<RuntimeServiceKind, Arc<Mutex<()>>>,
    repo_root: Option<PathBuf>,
    client: reqwest::Client,
}

impl RuntimeServiceSupervisor {
    pub fn from_environment() -> Self {
        let repo_root = std::env::var_os("AGENT_FRIEND_REPO_ROOT")
            .map(PathBuf::from)
            .and_then(|path| path.canonicalize().ok());
        Self::new(repo_root)
    }

    fn new(repo_root: Option<PathBuf>) -> Self {
        let slots = [RuntimeServiceKind::Bridge, RuntimeServiceKind::Voice]
            .into_iter()
            .map(|service| (service, Arc::new(Mutex::new(ServiceSlot::new(service)))))
            .collect();
        let operations = [RuntimeServiceKind::Bridge, RuntimeServiceKind::Voice]
            .into_iter()
            .map(|service| (service, Arc::new(Mutex::new(()))))
            .collect();
        Self {
            slots,
            operations,
            repo_root,
            client: reqwest::Client::new(),
        }
    }

    fn slot(&self, service: RuntimeServiceKind) -> Arc<Mutex<ServiceSlot>> {
        self.slots
            .get(&service)
            .expect("known service kind")
            .clone()
    }

    fn operation(&self, service: RuntimeServiceKind) -> Arc<Mutex<()>> {
        self.operations
            .get(&service)
            .expect("known service kind")
            .clone()
    }

    pub async fn snapshot(&self, service: RuntimeServiceKind) -> RuntimeServiceSnapshot {
        self.slot(service).lock().await.snapshot.clone()
    }

    pub async fn start(
        &self,
        app: Option<&AppHandle>,
        service: RuntimeServiceKind,
    ) -> Result<RuntimeServiceSnapshot, String> {
        let operation_ref = self.operation(service);
        let _operation = operation_ref.lock().await;
        let slot_ref = self.slot(service);
        let mut slot = slot_ref.lock().await;
        if matches!(slot.snapshot.phase, RuntimeServicePhase::Ready) {
            return Ok(slot.snapshot.clone());
        }
        let generation = slot.begin(RuntimeServicePhase::Starting);
        emit(app, &slot.snapshot);

        let Some(repo_root) = self.repo_root.as_ref() else {
            slot.fail(
                RuntimeServicePhase::Unavailable,
                "source_runtime_unavailable",
                "AGENT_FRIEND_REPO_ROOT is not configured; packaged sidecars are a separate requirement"
                    .to_string(),
            );
            emit(app, &slot.snapshot);
            return Err(error_message(&slot.snapshot));
        };

        if port_is_occupied(service.port()) {
            slot.fail(
                RuntimeServicePhase::Failed,
                "port_conflict",
                format!(
                    "port {} is already occupied by a process not owned by this supervisor",
                    service.port()
                ),
            );
            emit(app, &slot.snapshot);
            return Err(error_message(&slot.snapshot));
        }

        let token = generate_token();
        match spawn_service(repo_root, service, &token) {
            Ok(child) => {
                slot.child = Some(child);
                slot.token = Some(token.clone());
            }
            Err(error) => {
                slot.fail(RuntimeServicePhase::Failed, "launch_failed", error);
                emit(app, &slot.snapshot);
                return Err(error_message(&slot.snapshot));
            }
        }
        drop(slot);

        for _ in 0..READINESS_ATTEMPTS {
            let mut slot = slot_ref.lock().await;
            if slot.snapshot.generation != generation {
                return Err("service operation was superseded".to_string());
            }
            if let Some(child) = slot.child.as_mut() {
                if let Ok(Some(status)) = child.try_wait() {
                    slot.child = None;
                    slot.token = None;
                    slot.fail(
                        RuntimeServicePhase::Failed,
                        "process_exited_early",
                        format!("service exited before readiness: {status}"),
                    );
                    emit(app, &slot.snapshot);
                    return Err(error_message(&slot.snapshot));
                }
            }
            drop(slot);
            match self.readiness(service, &token).await {
                Ok(true) => {
                    let mut slot = slot_ref.lock().await;
                    if slot.snapshot.generation != generation {
                        return Err("service operation was superseded".to_string());
                    }
                    slot.snapshot.phase = RuntimeServicePhase::Ready;
                    slot.snapshot.error = None;
                    emit(app, &slot.snapshot);
                    return Ok(slot.snapshot.clone());
                }
                Ok(false) | Err(_) => tokio::time::sleep(POLL_DELAY).await,
            }
        }

        let mut slot = slot_ref.lock().await;
        if slot.snapshot.generation != generation {
            return Err("service operation was superseded".to_string());
        }
        terminate_child(&mut slot.child);
        slot.token = None;
        slot.fail(
            RuntimeServicePhase::Failed,
            "readiness_timeout",
            "service did not become ready within 10 seconds".to_string(),
        );
        emit(app, &slot.snapshot);
        Err(error_message(&slot.snapshot))
    }

    pub async fn stop(
        &self,
        app: Option<&AppHandle>,
        service: RuntimeServiceKind,
    ) -> Result<RuntimeServiceSnapshot, String> {
        let operation_ref = self.operation(service);
        let _operation = operation_ref.lock().await;
        self.stop_locked(app, service).await
    }

    async fn stop_locked(
        &self,
        app: Option<&AppHandle>,
        service: RuntimeServiceKind,
    ) -> Result<RuntimeServiceSnapshot, String> {
        let slot_ref = self.slot(service);
        let mut slot = slot_ref.lock().await;
        if slot.child.is_none() {
            slot.snapshot.phase = RuntimeServicePhase::Stopped;
            slot.snapshot.error = None;
            return Ok(slot.snapshot.clone());
        }
        slot.begin(RuntimeServicePhase::Quiescing);
        emit(app, &slot.snapshot);
        let token = slot
            .token
            .clone()
            .ok_or_else(|| "owned service is missing its lifecycle token".to_string())?;
        let generation = slot.snapshot.generation;
        drop(slot);

        let response = self.request_shutdown(service, &token).await;
        let mut slot = slot_ref.lock().await;
        if slot.snapshot.generation != generation {
            return Err("service operation was superseded".to_string());
        }
        match response {
            Ok(status) if status.is_success() => {}
            Ok(status) if status == reqwest::StatusCode::CONFLICT => {
                slot.fail(
                    RuntimeServicePhase::Blocked,
                    "voice_activity_blocked",
                    "active or connecting voice call blocks restart".to_string(),
                );
                emit(app, &slot.snapshot);
                return Err(error_message(&slot.snapshot));
            }
            Ok(status) => {
                slot.fail(
                    RuntimeServicePhase::Failed,
                    "shutdown_rejected",
                    format!("lifecycle shutdown returned HTTP {status}"),
                );
                emit(app, &slot.snapshot);
                return Err(error_message(&slot.snapshot));
            }
            Err(error) => {
                slot.fail(RuntimeServicePhase::Failed, "control_request_failed", error);
                emit(app, &slot.snapshot);
                return Err(error_message(&slot.snapshot));
            }
        }

        slot.snapshot.phase = RuntimeServicePhase::Stopping;
        emit(app, &slot.snapshot);
        drop(slot);
        for _ in 0..25 {
            let mut slot = slot_ref.lock().await;
            if slot.snapshot.generation != generation {
                return Err("service operation was superseded".to_string());
            }
            if let Some(child) = slot.child.as_mut() {
                match child.try_wait() {
                    Ok(Some(_)) => {
                        slot.child = None;
                        slot.token = None;
                        slot.snapshot.phase = RuntimeServicePhase::Stopped;
                        slot.snapshot.error = None;
                        emit(app, &slot.snapshot);
                        return Ok(slot.snapshot.clone());
                    }
                    Ok(None) => {}
                    Err(error) => {
                        slot.fail(
                            RuntimeServicePhase::Failed,
                            "process_wait_failed",
                            error.to_string(),
                        );
                        emit(app, &slot.snapshot);
                        return Err(error_message(&slot.snapshot));
                    }
                }
            }
            drop(slot);
            tokio::time::sleep(POLL_DELAY).await;
        }

        let mut slot = slot_ref.lock().await;
        if slot.snapshot.generation != generation {
            return Err("service operation was superseded".to_string());
        }
        terminate_child(&mut slot.child);
        slot.token = None;
        slot.fail(
            RuntimeServicePhase::Failed,
            "shutdown_timeout",
            "service required forced termination after shutdown timeout".to_string(),
        );
        emit(app, &slot.snapshot);
        Err(error_message(&slot.snapshot))
    }

    pub async fn restart(
        &self,
        app: Option<&AppHandle>,
        service: RuntimeServiceKind,
    ) -> Result<RuntimeServiceSnapshot, String> {
        self.stop(app, service).await?;
        self.start(app, service).await
    }

    pub async fn cleanup_owned_children(&self) {
        for service in [RuntimeServiceKind::Bridge, RuntimeServiceKind::Voice] {
            let slot_ref = self.slot(service);
            let mut slot = slot_ref.lock().await;
            terminate_child(&mut slot.child);
            slot.token = None;
            slot.snapshot.phase = RuntimeServicePhase::Stopped;
        }
    }

    async fn readiness(&self, service: RuntimeServiceKind, token: &str) -> Result<bool, String> {
        let response = self
            .client
            .get(format!(
                "{}/internal/lifecycle/readiness",
                service.base_url()
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        if !response.status().is_success() {
            return Ok(false);
        }
        let body: serde_json::Value = response.json().await.map_err(|error| error.to_string())?;
        Ok(body.get("status").and_then(|value| value.as_str()) == Some("ready"))
    }

    async fn request_shutdown(
        &self,
        service: RuntimeServiceKind,
        token: &str,
    ) -> Result<reqwest::StatusCode, String> {
        let request = self
            .client
            .post(format!(
                "{}/internal/lifecycle/shutdown",
                service.base_url()
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token);
        let request = if service == RuntimeServiceKind::Bridge {
            request.json(&serde_json::json!({
                "grace_timeout": 5.0,
                "cancel_timeout": 5.0
            }))
        } else {
            request
        };
        request
            .send()
            .await
            .map(|response| response.status())
            .map_err(|error| error.to_string())
    }
}

fn spawn_service(root: &Path, service: RuntimeServiceKind, token: &str) -> Result<Child, String> {
    Command::new("uv")
        .args(["run", "python", "-m", service.module()])
        .current_dir(root)
        .env("AGENT_FRIEND_LIFECYCLE_TOKEN", token)
        .env("AGENT_BRIDGE_DEV_MODE", "true")
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|error| format!("failed to launch {}: {error}", service.module()))
}

fn port_is_occupied(port: u16) -> bool {
    TcpStream::connect_timeout(
        &SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port),
        Duration::from_millis(150),
    )
    .is_ok()
}

fn generate_token() -> String {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn terminate_child(child: &mut Option<Child>) {
    if let Some(mut owned) = child.take() {
        let _ = owned.kill();
        let _ = owned.wait();
    }
}

fn emit(app: Option<&AppHandle>, snapshot: &RuntimeServiceSnapshot) {
    if let Some(app) = app {
        if let Err(error) = app.emit(EVENT_CHANGED, snapshot.clone()) {
            log::warn!("runtime service state event failed: {error}");
        }
    }
}

fn error_message(snapshot: &RuntimeServiceSnapshot) -> String {
    snapshot
        .error
        .as_ref()
        .map(|error| format!("{}: {}", error.code, error.message))
        .unwrap_or_else(|| "runtime service operation failed".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_is_random_and_not_empty() {
        let first = generate_token();
        let second = generate_token();
        assert_eq!(first.len(), 64);
        assert_ne!(first, second);
    }

    #[test]
    fn service_mapping_is_explicit() {
        assert_eq!(RuntimeServiceKind::Bridge.module(), "agent_bridge");
        assert_eq!(RuntimeServiceKind::Bridge.port(), 18800);
        assert_eq!(RuntimeServiceKind::Voice.module(), "voice_bridge");
        assert_eq!(RuntimeServiceKind::Voice.port(), 18900);
    }

    #[test]
    fn missing_source_root_is_explicitly_unavailable() {
        tauri::async_runtime::block_on(async {
            let supervisor = RuntimeServiceSupervisor::new(None);
            let error = supervisor
                .start(None, RuntimeServiceKind::Bridge)
                .await
                .expect_err("missing root must fail");
            assert!(error.contains("source_runtime_unavailable"));
            assert_eq!(
                supervisor.snapshot(RuntimeServiceKind::Bridge).await.phase,
                RuntimeServicePhase::Unavailable
            );
        });
    }
}
