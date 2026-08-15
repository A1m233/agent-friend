use super::model::{
    BackgroundDemandSnapshot, RuntimeServiceError, RuntimeServiceKind, RuntimeServicePhase,
    RuntimeServiceSnapshot,
};
use crate::{install_contract, paths};
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

#[cfg(target_os = "windows")]
struct PackagedProcessJob {
    handle: windows::Win32::Foundation::HANDLE,
    assigned_count: std::sync::atomic::AtomicUsize,
}

// A Job Object handle is a process-wide kernel handle. Assigning child process handles and
// closing it are thread-safe Win32 operations; ownership remains unique in this wrapper.
#[cfg(target_os = "windows")]
unsafe impl Send for PackagedProcessJob {}
#[cfg(target_os = "windows")]
unsafe impl Sync for PackagedProcessJob {}

#[cfg(target_os = "windows")]
impl PackagedProcessJob {
    fn new() -> Result<Self, String> {
        use windows::{
            core::PCWSTR,
            Win32::System::JobObjects::{
                CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
                JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            },
        };
        let handle =
            unsafe { CreateJobObjectW(None, PCWSTR::null()) }.map_err(|error| error.to_string())?;
        let mut information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if let Err(error) = unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                std::ptr::addr_of!(information).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } {
            unsafe {
                let _ = windows::Win32::Foundation::CloseHandle(handle);
            }
            return Err(error.to_string());
        }
        Ok(Self {
            handle,
            assigned_count: std::sync::atomic::AtomicUsize::new(0),
        })
    }

    fn assign(&self, child: &Child) -> Result<(), String> {
        use std::os::windows::io::AsRawHandle;
        use windows::Win32::{Foundation::HANDLE, System::JobObjects::AssignProcessToJobObject};
        let process = HANDLE(child.as_raw_handle());
        unsafe { AssignProcessToJobObject(self.handle, process) }
            .map_err(|error| error.to_string())?;
        self.assigned_count
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        Ok(())
    }

    fn assigned_count(&self) -> usize {
        self.assigned_count
            .load(std::sync::atomic::Ordering::Relaxed)
    }
}

#[cfg(target_os = "windows")]
impl Drop for PackagedProcessJob {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.handle);
        }
    }
}

#[cfg(not(target_os = "windows"))]
struct PackagedProcessJob;

#[cfg(not(target_os = "windows"))]
impl PackagedProcessJob {
    fn new() -> Result<Self, String> {
        Ok(Self)
    }

    fn assign(&self, _child: &Child) -> Result<(), String> {
        Ok(())
    }

    fn assigned_count(&self) -> usize {
        0
    }
}

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
        let error = RuntimeServiceError {
            code: code.to_string(),
            message,
            retryable: true,
        };
        self.snapshot.error = Some(error.clone());
        self.snapshot.last_error = Some(error);
    }

    fn reject_stop_while_running(&mut self, code: &str, message: String) -> String {
        let error = RuntimeServiceError {
            code: code.to_string(),
            message,
            retryable: true,
        };
        let result = format!("{}: {}", error.code, error.message);
        self.snapshot.phase = RuntimeServicePhase::Ready;
        self.snapshot.error = None;
        self.snapshot.last_error = Some(error);
        result
    }
}

pub struct RuntimeServiceSupervisor {
    slots: HashMap<RuntimeServiceKind, Arc<Mutex<ServiceSlot>>>,
    operations: HashMap<RuntimeServiceKind, Arc<Mutex<()>>>,
    launch_context: RuntimeLaunchContext,
    voice_public_url: Mutex<Option<String>>,
    tunnel_child: Mutex<Option<Child>>,
    client: reqwest::Client,
    process_job: Option<Arc<PackagedProcessJob>>,
    observed_readiness: Mutex<HashMap<RuntimeServiceKind, serde_json::Value>>,
}

#[derive(Clone, Debug)]
enum RuntimeLaunchContext {
    Source(PathBuf),
    Packaged {
        launcher: PathBuf,
        memory_model_dir: PathBuf,
        identity: install_contract::BuildIdentity,
        identity_sha256: String,
        profile: install_contract::InstallProfile,
    },
    Unavailable(String),
}

impl RuntimeServiceSupervisor {
    pub fn from_environment(resource_dir: Option<PathBuf>) -> Self {
        let repo_root = std::env::var_os("AGENT_FRIEND_REPO_ROOT")
            .map(PathBuf::from)
            .and_then(|path| path.canonicalize().ok());
        let launch_context = match repo_root {
            Some(path) => RuntimeLaunchContext::Source(path),
            None => match discover_packaged_runtime(resource_dir.as_deref()) {
                Ok(Some(context)) => context,
                Ok(None) => RuntimeLaunchContext::Unavailable(
                    "runtime_unavailable: packaged build identity is unavailable".to_string(),
                ),
                Err(error) => {
                    log::error!("packaged runtime rejected: {error}");
                    RuntimeLaunchContext::Unavailable(error)
                }
            },
        };
        Self::new(launch_context)
    }

    fn new(launch_context: RuntimeLaunchContext) -> Self {
        let (launch_context, process_job) =
            if matches!(launch_context, RuntimeLaunchContext::Packaged { .. }) {
                match PackagedProcessJob::new() {
                    Ok(job) => (launch_context, Some(Arc::new(job))),
                    Err(error) => (
                        RuntimeLaunchContext::Unavailable(format!(
                            "runtime_job_create_failed: {error}"
                        )),
                        None,
                    ),
                }
            } else {
                (launch_context, None)
            };
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
            process_job,
            observed_readiness: Mutex::new(HashMap::new()),
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
                self.launch_context.base_url(RuntimeServiceKind::Bridge)
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
                self.launch_context.base_url(RuntimeServiceKind::Bridge)
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
                self.launch_context.base_url(RuntimeServiceKind::Bridge)
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
                self.launch_context.base_url(RuntimeServiceKind::Bridge)
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
                self.launch_context.base_url(RuntimeServiceKind::Bridge)
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

        if let RuntimeLaunchContext::Unavailable(error) = &self.launch_context {
            slot.fail(
                RuntimeServicePhase::Unavailable,
                "install_integrity_unavailable",
                error.clone(),
            );
            emit(app, &slot.snapshot);
            return Err(error_message(&slot.snapshot));
        }

        let service_port = self.launch_context.port(service);
        if port_is_occupied(service_port) {
            slot.fail(
                RuntimeServicePhase::Failed,
                "port_conflict",
                format!(
                    "port {} is already occupied by a process not owned by this supervisor",
                    service_port
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
            self.process_job.as_deref(),
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
                Err(error) if error.starts_with("runtime_identity_mismatch") => {
                    let mut slot = slot_ref.lock().await;
                    terminate_child(&mut slot.child);
                    slot.token = None;
                    slot.fail(
                        RuntimeServicePhase::Failed,
                        "runtime_identity_mismatch",
                        error,
                    );
                    emit(app, &slot.snapshot);
                    return Err(error_message(&slot.snapshot));
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
                let error = slot.reject_stop_while_running(
                    "service_activity_blocked",
                    format!("active work blocks {} restart", service.module()),
                );
                emit(app, &slot.snapshot);
                return Err(error);
            }
            Ok(status) => {
                let error = slot.reject_stop_while_running(
                    "shutdown_rejected",
                    format!("lifecycle shutdown returned HTTP {status}"),
                );
                emit(app, &slot.snapshot);
                return Err(error);
            }
            Err(error) => {
                let error = slot.reject_stop_while_running("control_request_failed", error);
                emit(app, &slot.snapshot);
                return Err(error);
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
        let voice_port = self.launch_context.port(RuntimeServiceKind::Voice);
        let process_job = self.process_job.clone();

        self.stop(app, RuntimeServiceKind::Voice).await?;
        let tunnel_result = tokio::task::spawn_blocking(move || {
            spawn_quick_tunnel(&cloudflared, voice_port, process_job.as_deref())
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
            RuntimeLaunchContext::Unavailable(_) => {
                Err("tunnel_binary_missing: runtime launch context is unavailable".to_string())
            }
        }
    }

    async fn readiness(&self, service: RuntimeServiceKind, token: &str) -> Result<bool, String> {
        let response = self
            .client
            .get(format!(
                "{}/internal/lifecycle/readiness",
                self.launch_context.base_url(service)
            ))
            .header("X-Agent-Friend-Lifecycle-Token", token)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        if !response.status().is_success() {
            return Ok(false);
        }
        let body: serde_json::Value = response.json().await.map_err(|error| error.to_string())?;
        self.observed_readiness.lock().await.insert(
            service,
            serde_json::json!({
                "service": body.get("service"),
                "buildId": body.get("buildId"),
                "profileId": body.get("profileId"),
                "buildIdentitySha256": body.get("buildIdentitySha256"),
                "status": body.get("status"),
            }),
        );
        validate_readiness_identity(&body, service, &self.launch_context)
    }

    pub async fn install_integrity_runtime_evidence(&self) -> serde_json::Value {
        let observed = self.observed_readiness.lock().await;
        serde_json::json!({
            "observedReadiness": {
                "bridge": observed.get(&RuntimeServiceKind::Bridge),
                "voice": observed.get(&RuntimeServiceKind::Voice),
            },
            "jobObject": {
                "packaged": matches!(self.launch_context, RuntimeLaunchContext::Packaged { .. }),
                "created": self.process_job.is_some(),
                "assignedCount": self.process_job.as_ref().map(|job| job.assigned_count()).unwrap_or(0),
            }
        })
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
                self.launch_context.base_url(service)
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

fn validate_readiness_identity(
    body: &serde_json::Value,
    service: RuntimeServiceKind,
    launch_context: &RuntimeLaunchContext,
) -> Result<bool, String> {
    if body.get("status").and_then(|value| value.as_str()) != Some("ready") {
        return Ok(false);
    }
    if let RuntimeLaunchContext::Packaged {
        identity,
        identity_sha256,
        ..
    } = launch_context
    {
        let observed = (
            body.get("service").and_then(|value| value.as_str()),
            body.get("buildId").and_then(|value| value.as_str()),
            body.get("profileId").and_then(|value| value.as_str()),
            body.get("buildIdentitySha256")
                .and_then(|value| value.as_str()),
        );
        let expected = (
            service.command(),
            identity.build_id.as_str(),
            identity.profile_id.as_str(),
            identity_sha256.as_str(),
        );
        if observed
            != (
                Some(expected.0),
                Some(expected.1),
                Some(expected.2),
                Some(expected.3),
            )
        {
            return Err(format!(
                    "runtime_identity_mismatch: expected service/build/profile/hash {:?}, observed {:?}",
                    expected, observed
                ));
        }
    }
    Ok(true)
}

fn discover_packaged_runtime(
    resource_dir: Option<&Path>,
) -> Result<Option<RuntimeLaunchContext>, String> {
    let Some(active) = install_contract::discover_active_payload(resource_dir)? else {
        return Ok(None);
    };
    let runtime_root = active.runtime_root;
    let launcher = runtime_root.join("python").join("agent-friend-runtime.exe");
    let memory_model_dir = runtime_root.join("models").join("bge-small-zh-v1.5");
    if launcher.is_file() && memory_model_dir.is_dir() {
        Ok(Some(RuntimeLaunchContext::Packaged {
            launcher,
            memory_model_dir,
            identity: active.identity,
            identity_sha256: active.identity_sha256,
            profile: active.profile,
        }))
    } else {
        Err("install_integrity_required_runtime_missing".to_string())
    }
}

impl RuntimeLaunchContext {
    fn port(&self, service: RuntimeServiceKind) -> u16 {
        match self {
            Self::Packaged { profile, .. } => match service {
                RuntimeServiceKind::Bridge => profile.bridge_port,
                RuntimeServiceKind::Voice => profile.voice_port,
            },
            Self::Source(_) | Self::Unavailable(_) => service.port(),
        }
    }

    fn base_url(&self, service: RuntimeServiceKind) -> String {
        format!("http://127.0.0.1:{}", self.port(service))
    }
}

fn spawn_service(
    context: &RuntimeLaunchContext,
    service: RuntimeServiceKind,
    token: &str,
    voice_public_url: Option<&str>,
    process_job: Option<&PackagedProcessJob>,
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
            identity,
            profile,
            ..
        } => {
            let mut command = Command::new(launcher);
            command
                .args([service.command(), "--config-mode", "desktop"])
                .current_dir(launcher.parent().unwrap_or_else(|| Path::new(".")))
                .env("AGENT_FRIEND_MEMORY_MODEL_DIR", memory_model_dir)
                .env("AGENT_BRIDGE_DEV_MODE", "false")
                .env("AGENT_BRIDGE_PORT", profile.bridge_port.to_string())
                .env("VOICE_BRIDGE_PORT", profile.voice_port.to_string())
                .env(
                    "AGENT_BRIDGE_VOICE_BRIDGE_BASE_URL",
                    format!("http://127.0.0.1:{}", profile.voice_port),
                )
                .env(
                    "VOICE_BRIDGE_AGENT_BRIDGE_URL",
                    format!("http://127.0.0.1:{}", profile.bridge_port),
                )
                .env("AGENT_FRIEND_KEYRING_SERVICE", &profile.keyring_service)
                .env(
                    "AGENT_FRIEND_SETTINGS_BUNDLE_ID",
                    &profile.settings_namespace,
                )
                .env("AGENT_FRIEND_DATA_DIR", paths::user_data_dir())
                .env("AGENT_FRIEND_LOG_DIR", paths::log_dir())
                .env(
                    "AGENT_FRIEND_BUILD_IDENTITY_JSON",
                    serde_json::to_string(identity)
                        .map_err(|error| format!("runtime_identity_serialize_failed: {error}"))?,
                );
            if let Some((stdout, stderr)) = packaged_runtime_stdio(service) {
                command.stdout(stdout).stderr(stderr);
            } else {
                command.stdout(Stdio::null()).stderr(Stdio::null());
            }
            configure_packaged_process(&mut command);
            command
        }
        RuntimeLaunchContext::Unavailable(_) => {
            return Err("runtime launch context is unavailable".to_string())
        }
    };
    if let Some(public_url) = voice_public_url {
        command.env("VOICE_BRIDGE_PUBLIC_URL", public_url);
    }
    let mut child = command
        .env(
            "AGENT_FRIEND_MEMORY_INSPECTOR_ENABLED",
            if crate::owner_capabilities::local_capabilities().memory_inspector {
                "true"
            } else {
                "false"
            },
        )
        .env("AGENT_FRIEND_LIFECYCLE_TOKEN", token)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .spawn()
        .map_err(|error| format!("failed to launch {}: {error}", service.module()))?;
    if matches!(context, RuntimeLaunchContext::Packaged { .. }) {
        let job = process_job.ok_or_else(|| "runtime_job_unavailable".to_string())?;
        if let Err(error) = job.assign(&child) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("runtime_job_assign_failed: {error}"));
        }
    }
    Ok(child)
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

fn spawn_quick_tunnel(
    executable: &Path,
    port: u16,
    process_job: Option<&PackagedProcessJob>,
) -> Result<(Child, String), String> {
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
    if let Some(job) = process_job {
        if let Err(error) = job.assign(&child) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("tunnel_start_failed: job assign failed: {error}"));
        }
    }
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
    fn starting_a_retry_keeps_the_previous_failure_for_diagnostics() {
        let mut slot = ServiceSlot::new(RuntimeServiceKind::Bridge);
        slot.begin(RuntimeServicePhase::Starting);
        slot.fail(
            RuntimeServicePhase::Failed,
            "process_exited_early",
            "service exited".to_string(),
        );

        slot.begin(RuntimeServicePhase::Starting);

        assert!(slot.snapshot.error.is_none());
        assert_eq!(
            slot.snapshot
                .last_error
                .as_ref()
                .map(|error| error.code.as_str()),
            Some("process_exited_early")
        );
    }

    #[test]
    fn rejected_stop_keeps_the_running_service_ready() {
        let mut slot = ServiceSlot::new(RuntimeServiceKind::Voice);
        slot.begin(RuntimeServicePhase::Starting);
        slot.snapshot.phase = RuntimeServicePhase::Ready;

        let error = slot.reject_stop_while_running(
            "service_activity_blocked",
            "active work blocks voice_bridge restart".to_string(),
        );

        assert!(error.contains("service_activity_blocked"));
        assert_eq!(slot.snapshot.phase, RuntimeServicePhase::Ready);
        assert!(slot.snapshot.error.is_none());
        assert_eq!(
            slot.snapshot
                .last_error
                .as_ref()
                .map(|item| item.code.as_str()),
            Some("service_activity_blocked")
        );
    }

    #[test]
    fn missing_source_root_is_explicitly_unavailable() {
        tauri::async_runtime::block_on(async {
            let supervisor = RuntimeServiceSupervisor::new(RuntimeLaunchContext::Unavailable(
                "runtime_unavailable".to_string(),
            ));
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
                r"C:\Program Files\agent-friend\packages\build-123\runtime\python\agent-friend-runtime.exe",
            ),
            memory_model_dir: PathBuf::from(
                r"C:\Program Files\agent-friend\packages\build-123\runtime\models\bge-small-zh-v1.5",
            ),
            identity: install_contract::BuildIdentity {
                schema_version: 1,
                product_version: "1.2.3".to_string(),
                build_id: "build-123".to_string(),
                source_commit: "a".repeat(40),
                source_tree_sha256: "b".repeat(64),
                dirty: false,
                build_mode: "formal".to_string(),
                distribution: "ordinary".to_string(),
                profile_id: "production".to_string(),
            },
            identity_sha256: "c".repeat(64),
            profile: install_contract::embedded_profile().unwrap(),
        };
        match context {
            RuntimeLaunchContext::Packaged {
                launcher,
                memory_model_dir,
                ..
            } => {
                assert!(launcher.ends_with("agent-friend-runtime.exe"));
                assert!(memory_model_dir.ends_with("bge-small-zh-v1.5"));
            }
            _ => panic!("expected packaged context"),
        }
    }

    #[test]
    fn packaged_runtime_without_embedded_identity_is_not_guessed_from_paths() {
        let resource_dir = std::env::temp_dir().join(format!(
            "agent-friend-resource-discovery-{}",
            rand::random::<u64>()
        ));
        let runtime = resource_dir
            .join("packages")
            .join("build-123")
            .join("runtime");
        let launcher = runtime.join("python").join("agent-friend-runtime.exe");
        let model = runtime.join("models").join("bge-small-zh-v1.5");
        std::fs::create_dir_all(launcher.parent().unwrap()).unwrap();
        std::fs::create_dir_all(&model).unwrap();
        std::fs::write(&launcher, b"test").unwrap();

        let context = discover_packaged_runtime(Some(&resource_dir)).unwrap();

        assert!(context.is_none());
        std::fs::remove_dir_all(resource_dir).unwrap();
    }

    #[test]
    fn packaged_readiness_requires_exact_service_build_profile_and_hash() {
        let context = RuntimeLaunchContext::Packaged {
            launcher: PathBuf::from("runtime.exe"),
            memory_model_dir: PathBuf::from("model"),
            identity: install_contract::BuildIdentity {
                schema_version: 1,
                product_version: "1.2.3".to_string(),
                build_id: "build-123".to_string(),
                source_commit: "a".repeat(40),
                source_tree_sha256: "b".repeat(64),
                dirty: false,
                build_mode: "formal".to_string(),
                distribution: "ordinary".to_string(),
                profile_id: "production".to_string(),
            },
            identity_sha256: "c".repeat(64),
            profile: install_contract::embedded_profile().unwrap(),
        };
        let matching = serde_json::json!({
            "status": "ready",
            "service": "bridge",
            "buildId": "build-123",
            "profileId": "production",
            "buildIdentitySha256": "c".repeat(64),
        });
        assert!(
            validate_readiness_identity(&matching, RuntimeServiceKind::Bridge, &context).unwrap()
        );
        let mut stale = matching;
        stale["buildId"] = serde_json::json!("old-build");
        assert!(
            validate_readiness_identity(&stale, RuntimeServiceKind::Bridge, &context)
                .expect_err("stale runtime must be rejected")
                .starts_with("runtime_identity_mismatch")
        );
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn dropping_packaged_job_terminates_assigned_child() {
        let mut child = Command::new("cmd.exe")
            .args(["/C", "ping -n 30 127.0.0.1 > nul"])
            .spawn()
            .unwrap();
        let job = PackagedProcessJob::new().unwrap();
        job.assign(&child).unwrap();
        drop(job);

        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while std::time::Instant::now() < deadline {
            if child.try_wait().unwrap().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = child.kill();
        panic!("assigned child survived Job Object close");
    }
}
