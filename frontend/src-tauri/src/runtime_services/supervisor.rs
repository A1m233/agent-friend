use super::model::{
    BackgroundDemandSnapshot, RuntimeServiceError, RuntimeServiceKind, RuntimeServicePhase,
    RuntimeServiceSnapshot,
};
use crate::paths;
use rand::{rngs::OsRng, RngCore};
use std::{
    collections::HashMap,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader},
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{mpsc, Arc},
    thread,
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
    launch_context: RuntimeLaunchContext,
    voice_public_url: Mutex<Option<String>>,
    tunnel_child: Mutex<Option<Child>>,
    client: reqwest::Client,
}

#[derive(Clone, Debug)]
enum RuntimeLaunchContext {
    Source(PathBuf),
    Packaged {
        launcher: PathBuf,
        memory_model_dir: PathBuf,
    },
    Unavailable,
}

impl RuntimeServiceSupervisor {
    pub fn from_environment(resource_dir: Option<PathBuf>) -> Self {
        let repo_root = std::env::var_os("AGENT_FRIEND_REPO_ROOT")
            .map(PathBuf::from)
            .and_then(|path| path.canonicalize().ok());
        let launch_context = match repo_root {
            Some(path) => RuntimeLaunchContext::Source(path),
            None => discover_packaged_runtime(resource_dir.as_deref())
                .unwrap_or(RuntimeLaunchContext::Unavailable),
        };
        Self::new(launch_context)
    }

    fn new(launch_context: RuntimeLaunchContext) -> Self {
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
            launch_context,
            voice_public_url: Mutex::new(None),
            tunnel_child: Mutex::new(None),
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

    pub async fn background_demand(&self) -> Result<BackgroundDemandSnapshot, String> {
        let slot_ref = self.slot(RuntimeServiceKind::Bridge);
        let slot = slot_ref.lock().await;
        let token = slot
            .token
            .as_deref()
            .ok_or_else(|| "bridge_demand_unavailable: bridge is not owned or ready".to_string())?;
        let response = self
            .client
            .get(format!(
                "{}/internal/lifecycle/background-demand",
                RuntimeServiceKind::Bridge.base_url()
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token)
            .timeout(Duration::from_secs(1))
            .send()
            .await
            .map_err(|error| format!("bridge_demand_unavailable: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "bridge_demand_unavailable: HTTP {}",
                response.status()
            ));
        }
        response
            .json()
            .await
            .map_err(|error| format!("bridge_demand_unavailable: invalid response: {error}"))
    }

    pub async fn migration_snapshot_im_credentials(&self) -> Result<serde_json::Value, String> {
        let slot_ref = self.slot(RuntimeServiceKind::Bridge);
        let slot = slot_ref.lock().await;
        let token = slot
            .token
            .as_deref()
            .ok_or_else(|| "migration_bridge_not_ready".to_string())?;
        let response = self
            .client
            .get(format!(
                "{}/internal/migration/im-credentials",
                RuntimeServiceKind::Bridge.base_url()
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token)
            .send()
            .await
            .map_err(|error| format!("migration_im_snapshot_failed: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "migration_im_snapshot_failed: HTTP {}",
                response.status()
            ));
        }
        response
            .json()
            .await
            .map_err(|error| format!("migration_im_snapshot_invalid: {error}"))
    }

    pub async fn migration_inventory(&self) -> Result<serde_json::Value, String> {
        let slot_ref = self.slot(RuntimeServiceKind::Bridge);
        let slot = slot_ref.lock().await;
        let token = slot
            .token
            .as_deref()
            .ok_or_else(|| "migration_bridge_not_ready".to_string())?;
        let response = self
            .client
            .get(format!(
                "{}/internal/migration/inventory",
                RuntimeServiceKind::Bridge.base_url()
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token)
            .send()
            .await
            .map_err(|error| format!("migration_inventory_failed: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "migration_inventory_failed: HTTP {}",
                response.status()
            ));
        }
        response
            .json()
            .await
            .map_err(|error| format!("migration_inventory_invalid: {error}"))
    }

    pub async fn migration_replace_im_credentials(
        &self,
        payload: &serde_json::Value,
    ) -> Result<(), String> {
        let slot_ref = self.slot(RuntimeServiceKind::Bridge);
        let slot = slot_ref.lock().await;
        let token = slot
            .token
            .as_deref()
            .ok_or_else(|| "migration_bridge_not_ready".to_string())?;
        let response = self
            .client
            .post(format!(
                "{}/internal/migration/im-credentials/replace",
                RuntimeServiceKind::Bridge.base_url()
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token)
            .json(payload)
            .send()
            .await
            .map_err(|error| format!("migration_im_restore_failed: {error}"))?;
        if response.status().is_success() {
            Ok(())
        } else {
            Err(format!(
                "migration_im_restore_failed: HTTP {}",
                response.status()
            ))
        }
    }

    pub async fn migration_rebuild_memory_index(&self) -> Result<(), String> {
        let slot_ref = self.slot(RuntimeServiceKind::Bridge);
        let slot = slot_ref.lock().await;
        let token = slot
            .token
            .as_deref()
            .ok_or_else(|| "migration_bridge_not_ready".to_string())?;
        let response = self
            .client
            .post(format!(
                "{}/internal/migration/memory-index/rebuild",
                RuntimeServiceKind::Bridge.base_url()
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token)
            .send()
            .await
            .map_err(|error| format!("migration_memory_index_rebuild_failed: {error}"))?;
        if response.status().is_success() {
            Ok(())
        } else {
            Err(format!(
                "migration_memory_index_rebuild_failed: HTTP {}",
                response.status()
            ))
        }
    }

    pub async fn start(
        &self,
        app: Option<&AppHandle>,
        service: RuntimeServiceKind,
    ) -> Result<RuntimeServiceSnapshot, String> {
        let operation_ref = self.operation(service);
        let _operation = operation_ref.lock().await;
        let voice_public_url = if service == RuntimeServiceKind::Voice {
            self.voice_public_url.lock().await.clone()
        } else {
            None
        };
        let slot_ref = self.slot(service);
        let mut slot = slot_ref.lock().await;
        if matches!(slot.snapshot.phase, RuntimeServicePhase::Ready) {
            return Ok(slot.snapshot.clone());
        }
        let generation = slot.begin(RuntimeServicePhase::Starting);
        emit(app, &slot.snapshot);

        if matches!(self.launch_context, RuntimeLaunchContext::Unavailable) {
            slot.fail(
                RuntimeServicePhase::Unavailable,
                "runtime_unavailable",
                "neither a source runtime nor the packaged runtime resources are available"
                    .to_string(),
            );
            emit(app, &slot.snapshot);
            return Err(error_message(&slot.snapshot));
        }

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
        match spawn_service(
            &self.launch_context,
            service,
            &token,
            voice_public_url.as_deref(),
        ) {
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
        // The service already accepted the authenticated shutdown request and did not report
        // blocking activity. Once the supervisor has reaped its owned child, the requested
        // terminal state is reached even if graceful shutdown exceeded the deadline.
        log::warn!(
            "runtime service {} required forced termination after shutdown timeout",
            service.module()
        );
        slot.snapshot.phase = RuntimeServicePhase::Stopped;
        slot.snapshot.error = None;
        emit(app, &slot.snapshot);
        Ok(slot.snapshot.clone())
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
        terminate_child(&mut *self.tunnel_child.lock().await);
        *self.voice_public_url.lock().await = None;
    }

    pub async fn prepare_voice_runtime(
        &self,
        app: Option<&AppHandle>,
    ) -> Result<RuntimeServiceSnapshot, String> {
        if self.tunnel_child.lock().await.is_some() {
            return self.start(app, RuntimeServiceKind::Voice).await;
        }
        let cloudflared = self.cloudflared_executable()?;

        self.stop(app, RuntimeServiceKind::Voice).await?;
        let tunnel_result = tokio::task::spawn_blocking(move || {
            spawn_quick_tunnel(&cloudflared, RuntimeServiceKind::Voice.port())
        })
        .await
        .map_err(|error| format!("tunnel_task_failed: cloudflared task failed: {error}"))?;
        let (child, public_url) = match tunnel_result {
            Ok(result) => result,
            Err(error) => {
                // Tunnel failure must not take down local voice input. Restore local-mode voice
                // before surfacing the call-specific failure.
                let restore = self.start(app, RuntimeServiceKind::Voice).await;
                return Err(match restore {
                    Ok(_) => error,
                    Err(restore_error) => {
                        format!("{error}; voice_restore_failed: {restore_error}")
                    }
                });
            }
        };
        *self.tunnel_child.lock().await = Some(child);
        *self.voice_public_url.lock().await = Some(public_url);
        match self.start(app, RuntimeServiceKind::Voice).await {
            Ok(snapshot) => Ok(snapshot),
            Err(error) => {
                terminate_child(&mut *self.tunnel_child.lock().await);
                *self.voice_public_url.lock().await = None;
                Err(error)
            }
        }
    }

    pub async fn stop_voice_tunnel(
        &self,
        app: Option<&AppHandle>,
    ) -> Result<RuntimeServiceSnapshot, String> {
        let had_tunnel = self.tunnel_child.lock().await.is_some()
            || self.voice_public_url.lock().await.is_some();
        if !had_tunnel {
            return self.start(app, RuntimeServiceKind::Voice).await;
        }
        terminate_child(&mut *self.tunnel_child.lock().await);
        *self.voice_public_url.lock().await = None;
        self.restart(app, RuntimeServiceKind::Voice).await
    }

    fn cloudflared_executable(&self) -> Result<PathBuf, String> {
        match &self.launch_context {
            RuntimeLaunchContext::Source(_) => std::env::var_os("AGENT_FRIEND_CLOUDFLARED_PATH")
                .map(PathBuf::from)
                .ok_or_else(|| {
                    "tunnel_binary_missing: AGENT_FRIEND_CLOUDFLARED_PATH is not configured"
                        .to_string()
                }),
            RuntimeLaunchContext::Packaged { launcher, .. } => {
                let executable = launcher
                    .parent()
                    .and_then(Path::parent)
                    .map(|root| root.join("vendor").join("cloudflared.exe"))
                    .ok_or_else(|| {
                        "tunnel_binary_missing: invalid packaged runtime layout".to_string()
                    })?;
                if !executable.is_file() {
                    return Err(format!(
                        "tunnel_binary_missing: packaged cloudflared is missing: {}",
                        executable.display()
                    ));
                }
                Ok(executable)
            }
            RuntimeLaunchContext::Unavailable => {
                Err("tunnel_binary_missing: runtime launch context is unavailable".to_string())
            }
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

fn discover_packaged_runtime(resource_dir: Option<&Path>) -> Option<RuntimeLaunchContext> {
    let runtime_root = resource_dir?.join("runtime");
    let launcher = runtime_root.join("python").join("agent-friend-runtime.exe");
    let memory_model_dir = runtime_root.join("models").join("bge-small-zh-v1.5");
    if launcher.is_file() && memory_model_dir.is_dir() {
        Some(RuntimeLaunchContext::Packaged {
            launcher,
            memory_model_dir,
        })
    } else {
        None
    }
}

fn spawn_service(
    context: &RuntimeLaunchContext,
    service: RuntimeServiceKind,
    token: &str,
    voice_public_url: Option<&str>,
) -> Result<Child, String> {
    let mut command = match context {
        RuntimeLaunchContext::Source(root) => {
            let mut command = Command::new("uv");
            command
                .args(["run", "python", "-m", service.module()])
                .current_dir(root)
                .env("AGENT_BRIDGE_DEV_MODE", "true")
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit());
            command
        }
        RuntimeLaunchContext::Packaged {
            launcher,
            memory_model_dir,
        } => {
            let mut command = Command::new(launcher);
            command
                .args([service.command(), "--config-mode", "desktop"])
                .current_dir(launcher.parent().unwrap_or_else(|| Path::new(".")))
                .env("AGENT_FRIEND_MEMORY_MODEL_DIR", memory_model_dir)
                .env("AGENT_BRIDGE_DEV_MODE", "false");
            if let Some((stdout, stderr)) = packaged_runtime_stdio(service) {
                command.stdout(stdout).stderr(stderr);
            } else {
                command.stdout(Stdio::null()).stderr(Stdio::null());
            }
            configure_packaged_process(&mut command);
            command
        }
        RuntimeLaunchContext::Unavailable => {
            return Err("runtime launch context is unavailable".to_string())
        }
    };
    if let Some(public_url) = voice_public_url {
        command.env("VOICE_BRIDGE_PUBLIC_URL", public_url);
    }
    command
        .env("AGENT_FRIEND_LIFECYCLE_TOKEN", token)
        .stdin(Stdio::null())
        .spawn()
        .map_err(|error| format!("failed to launch {}: {error}", service.module()))
}

fn packaged_runtime_stdio(service: RuntimeServiceKind) -> Option<(Stdio, Stdio)> {
    let log_dir = paths::log_dir();
    if let Err(error) = fs::create_dir_all(&log_dir) {
        log::warn!("failed to create packaged runtime log dir {log_dir:?}: {error}");
        return None;
    }
    let name = match service {
        RuntimeServiceKind::Bridge => "runtime-bridge.log",
        RuntimeServiceKind::Voice => "runtime-voice.log",
    };
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_dir.join(name))
        .map_err(|error| {
            log::warn!("failed to open packaged runtime log {name}: {error}");
            error
        })
        .ok()?;
    let stdout = file.try_clone().ok()?;
    Some((Stdio::from(stdout), Stdio::from(file)))
}

fn spawn_quick_tunnel(executable: &Path, port: u16) -> Result<(Child, String), String> {
    let mut command = Command::new(executable);
    command
        .args([
            "tunnel",
            "--no-autoupdate",
            "--url",
            &format!("http://127.0.0.1:{port}"),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    configure_packaged_process(&mut command);
    let mut child = command
        .spawn()
        .map_err(|error| format!("tunnel_start_failed: failed to launch cloudflared: {error}"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "tunnel_start_failed: cloudflared stderr pipe is unavailable".to_string())?;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let mut sent = false;
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            log::info!("cloudflared: {line}");
            if !sent {
                if let Some(url) = quick_tunnel_url(&line) {
                    let _ = sender.send(url);
                    sent = true;
                }
            }
        }
    });
    match receiver.recv_timeout(Duration::from_secs(25)) {
        Ok(url) => Ok((child, url)),
        Err(mpsc::RecvTimeoutError::Timeout) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(
                "tunnel_url_timeout: cloudflared did not provide a Quick Tunnel URL within 25 seconds"
                    .to_string(),
            )
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            let status = child
                .try_wait()
                .ok()
                .flatten()
                .map(|value| value.to_string())
                .unwrap_or_else(|| "unknown status".to_string());
            let _ = child.kill();
            let _ = child.wait();
            Err(format!(
                "tunnel_exited_early: cloudflared exited before providing a Quick Tunnel URL: {status}"
            ))
        }
    }
}

fn quick_tunnel_url(line: &str) -> Option<String> {
    line.split_whitespace()
        .map(|part| {
            part.trim_matches(|character: char| {
                matches!(character, '|' | ',' | ';' | '(' | ')' | '[' | ']')
            })
        })
        .find(|part| part.starts_with("https://") && part.contains(".trycloudflare.com"))
        .map(ToString::to_string)
}

#[cfg(target_os = "windows")]
fn configure_packaged_process(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(target_os = "windows"))]
fn configure_packaged_process(_command: &mut Command) {}

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
            let supervisor = RuntimeServiceSupervisor::new(RuntimeLaunchContext::Unavailable);
            let error = supervisor
                .start(None, RuntimeServiceKind::Bridge)
                .await
                .expect_err("missing root must fail");
            assert!(error.contains("runtime_unavailable"));
            assert_eq!(
                supervisor.snapshot(RuntimeServiceKind::Bridge).await.phase,
                RuntimeServicePhase::Unavailable
            );
        });
    }

    #[test]
    fn parses_quick_tunnel_url_from_cloudflared_output() {
        assert_eq!(
            quick_tunnel_url("INF | https://quiet-tree.trycloudflare.com |"),
            Some("https://quiet-tree.trycloudflare.com".to_string())
        );
        assert_eq!(quick_tunnel_url("INF retrying connection"), None);
    }

    #[test]
    fn packaged_paths_are_stable_and_do_not_require_python() {
        let context = RuntimeLaunchContext::Packaged {
            launcher: PathBuf::from(
                r"C:\Program Files\agent-friend\runtime\python\agent-friend-runtime.exe",
            ),
            memory_model_dir: PathBuf::from(
                r"C:\Program Files\agent-friend\runtime\models\bge-small-zh-v1.5",
            ),
        };
        match context {
            RuntimeLaunchContext::Packaged {
                launcher,
                memory_model_dir,
            } => {
                assert!(launcher.ends_with("agent-friend-runtime.exe"));
                assert!(memory_model_dir.ends_with("bge-small-zh-v1.5"));
            }
            _ => panic!("expected packaged context"),
        }
    }

    #[test]
    fn packaged_runtime_is_discovered_from_tauri_resource_dir() {
        let resource_dir = std::env::temp_dir().join(format!(
            "agent-friend-resource-discovery-{}",
            rand::random::<u64>()
        ));
        let runtime = resource_dir.join("runtime");
        let launcher = runtime.join("python").join("agent-friend-runtime.exe");
        let model = runtime.join("models").join("bge-small-zh-v1.5");
        std::fs::create_dir_all(launcher.parent().unwrap()).unwrap();
        std::fs::create_dir_all(&model).unwrap();
        std::fs::write(&launcher, b"test").unwrap();

        let context = discover_packaged_runtime(Some(&resource_dir));

        assert!(matches!(
            context,
            Some(RuntimeLaunchContext::Packaged { .. })
        ));
        std::fs::remove_dir_all(resource_dir).unwrap();
    }
}
