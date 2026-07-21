use super::{
    evaluator::{evaluate, PolicyDecision},
    model::{
        BridgeDemandState, InhibitorState, PlatformCapability, PowerDemandReason, PowerPolicyError,
        PowerPolicySnapshot, PowerSource,
    },
    platform::{PowerInhibitor, PowerPlatformAdapter},
};
use crate::{runtime_services::RuntimeServiceSupervisor, settings::Settings};
use std::{
    collections::BTreeSet,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex as StdMutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter};
use tokio::sync::{Mutex, Notify};

const RECONCILE_INTERVAL: Duration = Duration::from_secs(2);
const INHIBITOR_REASON: &str = "agent-friend is keeping Agent tasks and IM online";
pub(crate) const EVENT_CHANGED: &str = "power-policy://changed";

struct ManagerState {
    snapshot: PowerPolicySnapshot,
    inhibitor: Option<Box<dyn PowerInhibitor>>,
}

pub struct PowerPolicyManager {
    settings: Arc<StdMutex<Settings>>,
    supervisor: Arc<RuntimeServiceSupervisor>,
    adapter: Arc<dyn PowerPlatformAdapter>,
    state: Mutex<ManagerState>,
    notify: Notify,
    started: AtomicBool,
    stopping: AtomicBool,
}

impl PowerPolicyManager {
    pub(crate) fn new(
        settings: Arc<StdMutex<Settings>>,
        supervisor: Arc<RuntimeServiceSupervisor>,
        adapter: Arc<dyn PowerPlatformAdapter>,
    ) -> Self {
        let policy = settings
            .lock()
            .map(|settings| settings.background_online_policy)
            .unwrap_or_default();
        let capability = adapter.capability();
        Self {
            settings,
            supervisor,
            adapter,
            state: Mutex::new(ManagerState {
                snapshot: PowerPolicySnapshot::initial(policy, capability),
                inhibitor: None,
            }),
            notify: Notify::new(),
            started: AtomicBool::new(false),
            stopping: AtomicBool::new(false),
        }
    }

    pub fn start(self: &Arc<Self>, app: AppHandle) {
        if self.started.swap(true, Ordering::SeqCst) {
            return;
        }
        let manager = self.clone();
        tauri::async_runtime::spawn(async move {
            loop {
                if manager.stopping.load(Ordering::SeqCst) {
                    break;
                }
                manager.reconcile(&app).await;
                tokio::select! {
                    _ = tokio::time::sleep(RECONCILE_INTERVAL) => {},
                    _ = manager.notify.notified() => {},
                }
            }
        });
    }

    pub fn request_refresh(&self) {
        self.notify.notify_one();
    }

    pub async fn snapshot(&self) -> PowerPolicySnapshot {
        self.state.lock().await.snapshot.clone()
    }

    pub async fn refresh(&self, app: &AppHandle) -> PowerPolicySnapshot {
        self.reconcile(app).await;
        self.snapshot().await
    }

    pub fn open_power_settings(&self) -> Result<(), PowerPolicyError> {
        self.adapter.open_power_settings()
    }

    pub async fn shutdown(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        self.notify.notify_waiters();
        let mut state = self.state.lock().await;
        if let Some(inhibitor) = state.inhibitor.as_mut() {
            if let Err(error) = inhibitor.release() {
                log::warn!("power policy shutdown release failed: {}", error.message);
            }
        }
        state.inhibitor = None;
        state.snapshot.inhibitor_state = InhibitorState::Inactive;
    }

    async fn reconcile(&self, app: &AppHandle) {
        let policy = self
            .settings
            .lock()
            .map(|settings| settings.background_online_policy)
            .unwrap_or_default();
        let capability = self.adapter.capability();
        let (power_source, power_error) = if capability == PlatformCapability::Supported {
            match self.adapter.power_source() {
                Ok(source) => (source, None),
                Err(error) => (PowerSource::Unknown, Some(error)),
            }
        } else {
            (PowerSource::Unknown, None)
        };

        let (reasons, bridge_state, bridge_error) = match self.supervisor.background_demand().await
        {
            Ok(demand) => {
                let mut reasons = BTreeSet::new();
                if demand.agent_task.active {
                    reasons.insert(PowerDemandReason::AgentTask);
                }
                if demand.im_online.active {
                    reasons.insert(PowerDemandReason::ImOnline);
                }
                (reasons, BridgeDemandState::Available, None)
            }
            Err(message) => (
                BTreeSet::new(),
                BridgeDemandState::Unavailable,
                Some(PowerPolicyError::new("bridge_demand_unavailable", message)),
            ),
        };

        let decision = evaluate(policy, power_source, !reasons.is_empty(), capability);
        let mut state = self.state.lock().await;
        let previous = state.snapshot.clone();
        let action_error = apply_decision(&self.adapter, decision, &mut state);
        state.snapshot.policy = policy;
        state.snapshot.power_source = power_source;
        state.snapshot.reasons = reasons;
        state.snapshot.capability = capability;
        state.snapshot.bridge_state = bridge_state;
        state.snapshot.last_error = action_error.or(bridge_error).or(power_error);
        let next = state.snapshot.clone();
        drop(state);

        if next != previous {
            log::info!(
                "power policy reconciled policy={:?} source={:?} reasons={:?} state={:?} error={:?}",
                next.policy,
                next.power_source,
                next.reasons,
                next.inhibitor_state,
                next.last_error.as_ref().map(|error| error.code.as_str())
            );
            if let Err(error) = app.emit(EVENT_CHANGED, next) {
                log::warn!("power policy state event failed: {error}");
            }
        }
    }
}

fn apply_decision(
    adapter: &Arc<dyn PowerPlatformAdapter>,
    decision: PolicyDecision,
    state: &mut ManagerState,
) -> Option<PowerPolicyError> {
    if decision == PolicyDecision::Inhibit {
        if state.inhibitor.is_some() {
            state.snapshot.inhibitor_state = InhibitorState::Active;
            return None;
        }
        match adapter.acquire(INHIBITOR_REASON) {
            Ok(inhibitor) => {
                state.inhibitor = Some(inhibitor);
                state.snapshot.inhibitor_state = InhibitorState::Active;
                None
            }
            Err(error) => {
                state.snapshot.inhibitor_state = InhibitorState::Error;
                Some(error)
            }
        }
    } else if let Some(inhibitor) = state.inhibitor.as_mut() {
        match inhibitor.release() {
            Ok(()) => {
                state.inhibitor = None;
                state.snapshot.inhibitor_state = InhibitorState::Inactive;
                None
            }
            Err(error) => {
                state.snapshot.inhibitor_state = InhibitorState::Error;
                Some(error)
            }
        }
    } else {
        state.snapshot.inhibitor_state = InhibitorState::Inactive;
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::power_policy::model::BackgroundOnlinePolicy;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct FakeAdapter {
        acquired: Arc<AtomicUsize>,
        released: Arc<AtomicUsize>,
    }

    struct FakeInhibitor(Arc<AtomicUsize>);

    impl PowerInhibitor for FakeInhibitor {
        fn release(&mut self) -> Result<(), PowerPolicyError> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
    }

    impl PowerPlatformAdapter for FakeAdapter {
        fn capability(&self) -> PlatformCapability {
            PlatformCapability::Supported
        }
        fn power_source(&self) -> Result<PowerSource, PowerPolicyError> {
            Ok(PowerSource::Ac)
        }
        fn acquire(&self, _reason: &str) -> Result<Box<dyn PowerInhibitor>, PowerPolicyError> {
            self.acquired.fetch_add(1, Ordering::SeqCst);
            Ok(Box::new(FakeInhibitor(self.released.clone())))
        }
        fn open_power_settings(&self) -> Result<(), PowerPolicyError> {
            Ok(())
        }
    }

    #[test]
    fn apply_is_idempotent_and_releases_once() {
        let acquired = Arc::new(AtomicUsize::new(0));
        let released = Arc::new(AtomicUsize::new(0));
        let adapter: Arc<dyn PowerPlatformAdapter> = Arc::new(FakeAdapter {
            acquired: acquired.clone(),
            released: released.clone(),
        });
        let mut state = ManagerState {
            snapshot: PowerPolicySnapshot::initial(
                BackgroundOnlinePolicy::Always,
                PlatformCapability::Supported,
            ),
            inhibitor: None,
        };

        assert!(apply_decision(&adapter, PolicyDecision::Inhibit, &mut state).is_none());
        assert!(apply_decision(&adapter, PolicyDecision::Inhibit, &mut state).is_none());
        assert_eq!(acquired.load(Ordering::SeqCst), 1);
        assert!(apply_decision(&adapter, PolicyDecision::FollowSystem, &mut state).is_none());
        assert!(apply_decision(&adapter, PolicyDecision::FollowSystem, &mut state).is_none());
        assert_eq!(released.load(Ordering::SeqCst), 1);
    }
}
