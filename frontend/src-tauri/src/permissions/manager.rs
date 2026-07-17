use std::{path::PathBuf, sync::Arc, time::Duration};

use serde::{Deserialize, Serialize};
use tauri::Emitter;
use tokio::sync::Mutex;

use super::{
    flow::PermissionFlowCoordinator,
    history::PermissionHistory,
    model::{
        PermissionFlowFinished, PermissionFlowReason, PermissionFlowRequest,
        PermissionFlowSnapshot, PermissionFlowTicket, PermissionFlowTicketStatus, PermissionId,
        PermissionPresentationState, PermissionRecoveryAction, PermissionSnapshot,
        SystemPermissionObservation, SystemPermissionState,
    },
    platform::{PermissionPlatformAdapter, RequestInstruction},
    projection::{observe_system_state, project_permission},
    registry::enabled_permissions,
};

pub(crate) const EVENT_PERMISSIONS_CHANGED: &str = "permissions://changed";
pub(crate) const EVENT_PERMISSION_FLOW_REQUESTED: &str = "permissions://flow-requested";
pub(crate) const EVENT_PERMISSION_FLOW_FINISHED: &str = "permissions://flow-finished";
const REFRESH_DEBOUNCE: Duration = Duration::from_millis(150);

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PermissionRefreshReason {
    Startup,
    Resumed,
    Focused,
    Manual,
    AttemptCompleted,
}

struct PermissionManagerState {
    snapshot: PermissionSnapshot,
    history: PermissionHistory,
    refresh_in_flight: bool,
    refresh_requested: bool,
    refresh_schedule_revision: u64,
    flow: PermissionFlowCoordinator,
}

pub(crate) struct PermissionManager {
    publisher: Arc<dyn PermissionSnapshotPublisher>,
    adapter: Arc<dyn PermissionPlatformAdapter>,
    history_path: PathBuf,
    state: Mutex<PermissionManagerState>,
}

pub(crate) trait PermissionSnapshotPublisher: Send + Sync {
    fn publish(&self, snapshot: &PermissionSnapshot);
    fn flow_requested(&self, _flow: &PermissionFlowSnapshot) {}
    fn flow_finished(&self, _finished: &PermissionFlowFinished) {}
}

struct TauriPermissionSnapshotPublisher(tauri::AppHandle);

impl PermissionSnapshotPublisher for TauriPermissionSnapshotPublisher {
    fn publish(&self, snapshot: &PermissionSnapshot) {
        if let Err(error) = self.0.emit(EVENT_PERMISSIONS_CHANGED, snapshot.clone()) {
            log::warn!("permission snapshot emit failed: {error}");
        }
    }

    fn flow_requested(&self, flow: &PermissionFlowSnapshot) {
        if let Err(error) = self.0.emit(EVENT_PERMISSION_FLOW_REQUESTED, flow.clone()) {
            log::warn!("permission flow requested emit failed: {error}");
        }
        if let Err(error) = crate::show_permission_settings(&self.0) {
            log::warn!("open permission center failed: {error}");
        }
    }

    fn flow_finished(&self, finished: &PermissionFlowFinished) {
        if let Err(error) = self
            .0
            .emit(EVENT_PERMISSION_FLOW_FINISHED, finished.clone())
        {
            log::warn!("permission flow finished emit failed: {error}");
        }
    }
}

impl PermissionManager {
    pub(crate) fn new(
        app: tauri::AppHandle,
        adapter: Arc<dyn PermissionPlatformAdapter>,
        history_path: PathBuf,
    ) -> Self {
        Self::new_with_publisher(
            adapter,
            history_path,
            Arc::new(TauriPermissionSnapshotPublisher(app)),
        )
    }

    fn new_with_publisher(
        adapter: Arc<dyn PermissionPlatformAdapter>,
        history_path: PathBuf,
        publisher: Arc<dyn PermissionSnapshotPublisher>,
    ) -> Self {
        let history = PermissionHistory::load_or_default(&history_path);
        let items = enabled_permissions()
            .map(|definition| {
                project_permission(
                    definition,
                    &SystemPermissionObservation {
                        state: SystemPermissionState::Unknown,
                        recovery_action: PermissionRecoveryAction::None,
                    },
                    &history.entry(definition.id),
                )
            })
            .collect();
        Self {
            publisher,
            adapter,
            history_path,
            state: Mutex::new(PermissionManagerState {
                snapshot: PermissionSnapshot {
                    snapshot_revision: 0,
                    refreshing: false,
                    active_flow: None,
                    items,
                },
                history,
                refresh_in_flight: false,
                refresh_requested: false,
                refresh_schedule_revision: 0,
                flow: PermissionFlowCoordinator::default(),
            }),
        }
    }

    pub(crate) async fn snapshot(&self) -> PermissionSnapshot {
        self.state.lock().await.snapshot.clone()
    }

    pub(crate) async fn schedule_refresh(self: &Arc<Self>, reason: PermissionRefreshReason) {
        let schedule_revision = {
            let mut state = self.state.lock().await;
            state.refresh_schedule_revision += 1;
            state.refresh_schedule_revision
        };
        tokio::time::sleep(REFRESH_DEBOUNCE).await;
        let is_latest = {
            let state = self.state.lock().await;
            state.refresh_schedule_revision == schedule_revision
        };
        if is_latest {
            let _ = self.refresh(reason).await;
        }
    }

    pub(crate) async fn refresh(&self, reason: PermissionRefreshReason) -> PermissionSnapshot {
        {
            let mut state = self.state.lock().await;
            if state.refresh_in_flight {
                state.refresh_requested = true;
                return state.snapshot.clone();
            }
            state.refresh_in_flight = true;
            set_refreshing(self.publisher.as_ref(), &mut state.snapshot, true);
        }

        let mut current_reason = reason;
        loop {
            let observations = self.query_enabled_permissions().await;
            let (snapshot, repeat) = {
                let mut state = self.state.lock().await;
                let observed_at_ms = now_ms();
                for (definition, observation) in enabled_permissions().zip(observations.iter()) {
                    observe_system_state(
                        state.history.entry_mut(definition.id),
                        observation.state,
                        observed_at_ms,
                    );
                }
                if let Err(error) = state.history.save(&self.history_path) {
                    log::warn!("permission history save failed after {current_reason:?}: {error}");
                }

                let items = enabled_permissions()
                    .zip(observations.iter())
                    .map(|(definition, observation)| {
                        project_permission(
                            definition,
                            observation,
                            &state.history.entry(definition.id),
                        )
                    })
                    .collect::<Vec<_>>();
                update_items(self.publisher.as_ref(), &mut state.snapshot, items);

                if state.refresh_requested {
                    state.refresh_requested = false;
                    current_reason = PermissionRefreshReason::Manual;
                    (state.snapshot.clone(), true)
                } else {
                    state.refresh_in_flight = false;
                    set_refreshing(self.publisher.as_ref(), &mut state.snapshot, false);
                    (state.snapshot.clone(), false)
                }
            };
            if !repeat {
                let should_finish_granted_flow =
                    snapshot.active_flow.as_ref().is_some_and(|flow| {
                        flow.phase == super::model::PermissionFlowPhase::AwaitingUser
                            && flow.permission_ids.iter().all(|id| {
                                snapshot.item(*id).is_some_and(|item| {
                                    item.system_state == SystemPermissionState::Granted
                                })
                            })
                    });
                if should_finish_granted_flow {
                    self.finish_active_and_start_next().await;
                    return self.snapshot().await;
                }
                return snapshot;
            }
        }
    }

    pub(crate) async fn request_flow(
        &self,
        request: PermissionFlowRequest,
    ) -> PermissionFlowTicket {
        let mut state = self.state.lock().await;
        let target = flow_target(&request, &state.snapshot);
        let ticket = state.flow.request(request, target);
        let active = state.flow.active().cloned();
        update_active_flow(self.publisher.as_ref(), &mut state.snapshot, active.clone());
        if ticket.status == PermissionFlowTicketStatus::Started {
            if let Some(active) = active {
                self.publisher.flow_requested(&active);
            }
        }
        ticket
    }

    pub(crate) async fn begin_attempt(
        &self,
        flow_id: &str,
        permission_id: PermissionId,
    ) -> Result<RequestInstruction, String> {
        self.refresh(PermissionRefreshReason::Manual).await;
        let recovery_action = {
            let mut state = self.state.lock().await;
            let item = state
                .snapshot
                .item(permission_id)
                .cloned()
                .ok_or_else(|| "permission is not registered".to_string())?;
            if item.system_state == SystemPermissionState::Granted {
                drop(state);
                self.advance_or_finish_flow().await;
                return Err("permission is already granted; refresh the flow target".to_string());
            }
            state.flow.begin_attempt(flow_id, permission_id)?;
            let active = state.flow.active().cloned();
            update_active_flow(self.publisher.as_ref(), &mut state.snapshot, active);
            item.recovery_action
        };
        let instruction = match recovery_action {
            PermissionRecoveryAction::OpenSystemSettings => Ok(RequestInstruction::OpenSettings),
            PermissionRecoveryAction::None => Ok(RequestInstruction::NotAvailable),
            PermissionRecoveryAction::Request | PermissionRecoveryAction::RetryRequest => self
                .adapter
                .prepare_request(permission_id)
                .await
                .map_err(|error| format!("{}: {}", error.code, error.message)),
        };
        if instruction.is_err() {
            let mut state = self.state.lock().await;
            state.flow.abort_attempt(flow_id)?;
            let active = state.flow.active().cloned();
            update_active_flow(self.publisher.as_ref(), &mut state.snapshot, active);
        }
        instruction
    }

    pub(crate) async fn complete_attempt(
        &self,
        flow_id: &str,
        permission_id: PermissionId,
    ) -> Result<PermissionSnapshot, String> {
        {
            let mut state = self.state.lock().await;
            let active = state
                .flow
                .active()
                .ok_or_else(|| "no active permission flow".to_string())?;
            if active.flow_id != flow_id || active.current_permission_id != Some(permission_id) {
                return Err("permission attempt does not match the active flow".to_string());
            }
            if let Some(definition) = enabled_permissions().find(|item| item.id == permission_id) {
                let history = state.history.entry_mut(permission_id);
                history.handled_revision = definition.revision;
                history.deferred_revision = None;
            }
            state
                .history
                .save(&self.history_path)
                .map_err(|error| error.to_string())?;
            state.flow.mark_refreshing(flow_id)?;
            let active = state.flow.active().cloned();
            update_active_flow(self.publisher.as_ref(), &mut state.snapshot, active);
        }

        self.refresh(PermissionRefreshReason::AttemptCompleted)
            .await;
        self.advance_or_finish_flow().await;
        Ok(self.snapshot().await)
    }

    pub(crate) async fn defer_flow(&self, flow_id: &str) -> Result<PermissionSnapshot, String> {
        {
            let mut state = self.state.lock().await;
            let active = state
                .flow
                .active()
                .filter(|active| active.flow_id == flow_id)
                .cloned()
                .ok_or_else(|| "permission flow id does not match the active flow".to_string())?;
            if active.current_permission_id.is_some() {
                return Err("an active system permission attempt cannot be deferred".to_string());
            }
            for permission_id in &active.permission_ids {
                if let Some(definition) =
                    enabled_permissions().find(|item| item.id == *permission_id)
                {
                    let history = state.history.entry_mut(*permission_id);
                    history.handled_revision = definition.revision;
                    history.deferred_revision = Some(definition.revision);
                }
            }
            state
                .history
                .save(&self.history_path)
                .map_err(|error| error.to_string())?;
            let items = reproject_from_current(&state.snapshot, &state.history);
            update_items(self.publisher.as_ref(), &mut state.snapshot, items);
        }
        self.finish_active_and_start_next().await;
        Ok(self.snapshot().await)
    }

    pub(crate) async fn open_system_settings(
        &self,
        permission_id: PermissionId,
    ) -> Result<(), String> {
        self.adapter
            .open_system_settings(permission_id)
            .await
            .map_err(|error| format!("{}: {}", error.code, error.message))
    }

    async fn advance_or_finish_flow(&self) {
        let has_remaining = {
            let state = self.state.lock().await;
            state.flow.active().is_some_and(|active| {
                active.permission_ids.iter().any(|id| {
                    state.snapshot.item(*id).is_some_and(|item| {
                        item.system_state != SystemPermissionState::Granted
                            && item.recovery_action != PermissionRecoveryAction::None
                    })
                })
            })
        };
        if has_remaining {
            let mut state = self.state.lock().await;
            state.flow.await_next();
            let active = state.flow.active().cloned();
            update_active_flow(self.publisher.as_ref(), &mut state.snapshot, active);
        } else {
            self.finish_active_and_start_next().await;
        }
    }

    async fn finish_active_and_start_next(&self) {
        let mut state = self.state.lock().await;
        if let Some(finished_flow) = state.flow.finish_active() {
            let granted_permission_ids = finished_flow
                .permission_ids
                .iter()
                .copied()
                .filter(|id| {
                    state
                        .snapshot
                        .item(*id)
                        .is_some_and(|item| item.system_state == SystemPermissionState::Granted)
                })
                .collect();
            self.publisher.flow_finished(&PermissionFlowFinished {
                flow_id: finished_flow.flow_id,
                request_ids: finished_flow.joined_request_ids,
                granted_permission_ids,
            });
        }

        while let Some(request) = state.flow.pop_pending() {
            let target = flow_target(&request, &state.snapshot);
            let ticket = state.flow.request(request, target);
            if ticket.status == PermissionFlowTicketStatus::Started {
                if let Some(active) = state.flow.active() {
                    self.publisher.flow_requested(active);
                }
                break;
            }
        }
        let active = state.flow.active().cloned();
        update_active_flow(self.publisher.as_ref(), &mut state.snapshot, active);
    }

    async fn query_enabled_permissions(&self) -> Vec<SystemPermissionObservation> {
        let mut observations = Vec::new();
        for definition in enabled_permissions() {
            let observation = self
                .adapter
                .query_permission(definition.id)
                .await
                .unwrap_or_else(|error| {
                    log::warn!(
                        "permission query failed: id={:?} code={} message={}",
                        definition.id,
                        error.code,
                        error.message
                    );
                    SystemPermissionObservation {
                        state: SystemPermissionState::Unknown,
                        recovery_action: PermissionRecoveryAction::None,
                    }
                });
            observations.push(observation);
        }
        observations
    }
}

fn update_items(
    publisher: &dyn PermissionSnapshotPublisher,
    snapshot: &mut PermissionSnapshot,
    items: Vec<super::model::PermissionItemSnapshot>,
) {
    if snapshot.items != items {
        snapshot.items = items;
        publish_snapshot(publisher, snapshot);
    }
}

fn update_active_flow(
    publisher: &dyn PermissionSnapshotPublisher,
    snapshot: &mut PermissionSnapshot,
    active_flow: Option<PermissionFlowSnapshot>,
) {
    if snapshot.active_flow != active_flow {
        snapshot.active_flow = active_flow;
        publish_snapshot(publisher, snapshot);
    }
}

fn flow_target(
    request: &PermissionFlowRequest,
    snapshot: &PermissionSnapshot,
) -> Vec<PermissionId> {
    enabled_permissions()
        .filter(|definition| {
            request.permission_ids.is_empty() || request.permission_ids.contains(&definition.id)
        })
        .filter(|definition| {
            let Some(item) = snapshot.item(definition.id) else {
                return true;
            };
            if item.system_state == SystemPermissionState::Granted {
                return false;
            }
            match request.reason {
                PermissionFlowReason::Startup | PermissionFlowReason::Upgrade => {
                    definition.startup_policy == super::registry::StartupPromptPolicy::Proactive
                        && !item.handled_for_current_revision
                        && !item.deferred_for_current_revision
                }
                PermissionFlowReason::Revoked => matches!(
                    item.presentation_state,
                    PermissionPresentationState::Revoked | PermissionPresentationState::Restricted
                ),
                PermissionFlowReason::FeatureFallback | PermissionFlowReason::Manual => true,
            }
        })
        .map(|definition| definition.id)
        .collect()
}

fn reproject_from_current(
    snapshot: &PermissionSnapshot,
    history: &PermissionHistory,
) -> Vec<super::model::PermissionItemSnapshot> {
    enabled_permissions()
        .map(|definition| {
            let observation = snapshot
                .item(definition.id)
                .map(|item| SystemPermissionObservation {
                    state: item.system_state,
                    recovery_action: item.recovery_action,
                })
                .unwrap_or(SystemPermissionObservation {
                    state: SystemPermissionState::Unknown,
                    recovery_action: PermissionRecoveryAction::None,
                });
            project_permission(definition, &observation, &history.entry(definition.id))
        })
        .collect()
}

fn set_refreshing(
    publisher: &dyn PermissionSnapshotPublisher,
    snapshot: &mut PermissionSnapshot,
    refreshing: bool,
) {
    if snapshot.refreshing != refreshing {
        snapshot.refreshing = refreshing;
        publish_snapshot(publisher, snapshot);
    }
}

fn publish_snapshot(
    publisher: &dyn PermissionSnapshotPublisher,
    snapshot: &mut PermissionSnapshot,
) {
    snapshot.snapshot_revision += 1;
    publisher.publish(snapshot);
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::permissions::{
        model::{
            PermissionFlowReason, PermissionFlowRequest, PermissionId, PermissionPresentationState,
        },
        platform::{PermissionPlatformFuture, RequestInstruction},
    };
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Mutex as StdMutex,
    };

    #[derive(Default)]
    struct RecordingPublisher {
        snapshots: StdMutex<Vec<PermissionSnapshot>>,
        requested: StdMutex<Vec<PermissionFlowSnapshot>>,
        finished: StdMutex<Vec<PermissionFlowFinished>>,
    }

    impl PermissionSnapshotPublisher for RecordingPublisher {
        fn publish(&self, snapshot: &PermissionSnapshot) {
            self.snapshots.lock().unwrap().push(snapshot.clone());
        }

        fn flow_requested(&self, flow: &PermissionFlowSnapshot) {
            self.requested.lock().unwrap().push(flow.clone());
        }

        fn flow_finished(&self, finished: &PermissionFlowFinished) {
            self.finished.lock().unwrap().push(finished.clone());
        }
    }

    struct BlockingAdapter {
        calls: AtomicUsize,
        active: AtomicUsize,
        max_active: AtomicUsize,
        started: tokio::sync::Notify,
        release: tokio::sync::Notify,
    }

    impl BlockingAdapter {
        fn new() -> Self {
            Self {
                calls: AtomicUsize::new(0),
                active: AtomicUsize::new(0),
                max_active: AtomicUsize::new(0),
                started: tokio::sync::Notify::new(),
                release: tokio::sync::Notify::new(),
            }
        }
    }

    impl PermissionPlatformAdapter for BlockingAdapter {
        fn query_permission(
            &self,
            _id: PermissionId,
        ) -> PermissionPlatformFuture<'_, SystemPermissionObservation> {
            Box::pin(async move {
                self.calls.fetch_add(1, Ordering::SeqCst);
                let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
                self.max_active.fetch_max(active, Ordering::SeqCst);
                self.started.notify_one();
                self.release.notified().await;
                self.active.fetch_sub(1, Ordering::SeqCst);
                Ok(SystemPermissionObservation {
                    state: SystemPermissionState::Granted,
                    recovery_action: PermissionRecoveryAction::None,
                })
            })
        }

        fn prepare_request(
            &self,
            _id: PermissionId,
        ) -> PermissionPlatformFuture<'_, RequestInstruction> {
            Box::pin(async { Ok(RequestInstruction::NotAvailable) })
        }

        fn open_system_settings(&self, _id: PermissionId) -> PermissionPlatformFuture<'_, ()> {
            Box::pin(async { Ok(()) })
        }
    }

    fn test_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "agent-friend-permission-manager-{}-{name}.json",
            std::process::id()
        ))
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn concurrent_refreshes_are_single_flight_and_request_one_follow_up() {
        let path = test_path("single-flight");
        let _ = std::fs::remove_file(&path);
        let adapter = Arc::new(BlockingAdapter::new());
        let publisher = Arc::new(RecordingPublisher::default());
        let manager = Arc::new(PermissionManager::new_with_publisher(
            adapter.clone(),
            path.clone(),
            publisher.clone(),
        ));

        let first_manager = manager.clone();
        let first =
            tokio::spawn(
                async move { first_manager.refresh(PermissionRefreshReason::Manual).await },
            );
        adapter.started.notified().await;

        let second = manager.refresh(PermissionRefreshReason::Focused).await;
        assert!(second.refreshing);
        adapter.release.notify_one();
        adapter.started.notified().await;
        adapter.release.notify_one();

        let completed = first.await.unwrap();
        assert!(!completed.refreshing);
        assert_eq!(adapter.calls.load(Ordering::SeqCst), 2);
        assert_eq!(adapter.max_active.load(Ordering::SeqCst), 1);
        assert_eq!(
            completed
                .item(PermissionId::Microphone)
                .unwrap()
                .presentation_state,
            PermissionPresentationState::Granted
        );
        let published = publisher.snapshots.lock().unwrap();
        assert!(published
            .windows(2)
            .all(|pair| pair[0].snapshot_revision < pair[1].snapshot_revision));
        std::fs::remove_file(path).unwrap();
    }

    #[tokio::test]
    async fn matching_requests_join_and_defer_persists_current_revision() {
        use crate::permissions::platform::fake::FakePermissionPlatformAdapter;

        let path = test_path("join-defer");
        let _ = std::fs::remove_file(&path);
        let adapter = Arc::new(FakePermissionPlatformAdapter::new(
            [Ok(SystemPermissionObservation {
                state: SystemPermissionState::NotDetermined,
                recovery_action: PermissionRecoveryAction::Request,
            })],
            RequestInstruction::WebMedia,
        ));
        let publisher = Arc::new(RecordingPublisher::default());
        let manager =
            PermissionManager::new_with_publisher(adapter, path.clone(), publisher.clone());
        manager.refresh(PermissionRefreshReason::Manual).await;

        let request = |request_id: &str| PermissionFlowRequest {
            request_id: request_id.to_string(),
            reason: PermissionFlowReason::FeatureFallback,
            permission_ids: vec![PermissionId::Microphone],
            source_window: Some("chat".to_string()),
        };
        let started = manager.request_flow(request("voice-input")).await;
        let joined = manager.request_flow(request("voice-call")).await;
        assert_eq!(started.status, PermissionFlowTicketStatus::Started);
        assert_eq!(joined.status, PermissionFlowTicketStatus::Joined);
        let active = manager.snapshot().await.active_flow.unwrap();
        assert_eq!(active.joined_request_ids, ["voice-input", "voice-call"]);

        manager.defer_flow(&active.flow_id).await.unwrap();
        assert!(manager.snapshot().await.active_flow.is_none());
        let history = PermissionHistory::load(&path).unwrap();
        let microphone = history.entry(PermissionId::Microphone);
        assert_eq!(microphone.handled_revision, 1);
        assert_eq!(microphone.deferred_revision, Some(1));
        assert_eq!(publisher.requested.lock().unwrap().len(), 1);
        assert_eq!(
            publisher.finished.lock().unwrap()[0].request_ids,
            ["voice-input", "voice-call"]
        );
        std::fs::remove_file(path).unwrap();
    }

    #[tokio::test]
    async fn lifecycle_refresh_finishes_an_awaiting_flow_after_settings_grant() {
        use crate::permissions::platform::fake::FakePermissionPlatformAdapter;

        let path = test_path("settings-grant");
        let _ = std::fs::remove_file(&path);
        let adapter = Arc::new(FakePermissionPlatformAdapter::new(
            [
                Ok(SystemPermissionObservation {
                    state: SystemPermissionState::Denied,
                    recovery_action: PermissionRecoveryAction::OpenSystemSettings,
                }),
                Ok(SystemPermissionObservation {
                    state: SystemPermissionState::Granted,
                    recovery_action: PermissionRecoveryAction::None,
                }),
            ],
            RequestInstruction::OpenSettings,
        ));
        let publisher = Arc::new(RecordingPublisher::default());
        let manager =
            PermissionManager::new_with_publisher(adapter, path.clone(), publisher.clone());
        manager.refresh(PermissionRefreshReason::Manual).await;
        let ticket = manager
            .request_flow(PermissionFlowRequest {
                request_id: "settings-recovery".to_string(),
                reason: PermissionFlowReason::FeatureFallback,
                permission_ids: vec![PermissionId::Microphone],
                source_window: Some("voice-call".to_string()),
            })
            .await;
        assert_eq!(ticket.status, PermissionFlowTicketStatus::Started);

        let refreshed = manager.refresh(PermissionRefreshReason::Focused).await;

        assert!(refreshed.active_flow.is_none());
        assert_eq!(
            publisher.finished.lock().unwrap()[0].request_ids,
            ["settings-recovery"]
        );
        assert_eq!(
            publisher.finished.lock().unwrap()[0].granted_permission_ids,
            [PermissionId::Microphone]
        );
        std::fs::remove_file(path).unwrap();
    }

    #[tokio::test]
    async fn not_available_attempt_finishes_instead_of_trapping_the_flow() {
        use crate::permissions::platform::fake::FakePermissionPlatformAdapter;

        let path = test_path("not-available");
        let _ = std::fs::remove_file(&path);
        let unsupported = || {
            Ok(SystemPermissionObservation {
                state: SystemPermissionState::Unsupported,
                recovery_action: PermissionRecoveryAction::None,
            })
        };
        let adapter = Arc::new(FakePermissionPlatformAdapter::new(
            [unsupported(), unsupported(), unsupported()],
            RequestInstruction::NotAvailable,
        ));
        let publisher = Arc::new(RecordingPublisher::default());
        let manager =
            PermissionManager::new_with_publisher(adapter, path.clone(), publisher.clone());
        manager.refresh(PermissionRefreshReason::Manual).await;
        let ticket = manager
            .request_flow(PermissionFlowRequest {
                request_id: "unsupported-platform".to_string(),
                reason: PermissionFlowReason::FeatureFallback,
                permission_ids: vec![PermissionId::Microphone],
                source_window: Some("voice-call".to_string()),
            })
            .await;
        let flow_id = ticket.flow_id.unwrap();
        assert_eq!(
            manager
                .begin_attempt(&flow_id, PermissionId::Microphone)
                .await
                .unwrap(),
            RequestInstruction::NotAvailable
        );

        let snapshot = manager
            .complete_attempt(&flow_id, PermissionId::Microphone)
            .await
            .unwrap();

        assert!(snapshot.active_flow.is_none());
        assert_eq!(
            publisher.finished.lock().unwrap()[0].request_ids,
            ["unsupported-platform"]
        );
        std::fs::remove_file(path).unwrap();
    }
}
