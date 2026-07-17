use std::{future::Future, pin::Pin, sync::Arc};

use serde::{Deserialize, Serialize};

use super::model::{PermissionId, SystemPermissionObservation};

#[cfg(target_os = "windows")]
pub(crate) mod windows;

pub(crate) type PermissionPlatformFuture<'a, T> =
    Pin<Box<dyn Future<Output = Result<T, PermissionPlatformError>> + Send + 'a>>;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RequestInstruction {
    Native,
    WebMedia,
    OpenSettings,
    NotAvailable,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct PermissionPlatformError {
    pub code: String,
    pub message: String,
}

pub(crate) trait PermissionPlatformAdapter: Send + Sync {
    fn query_permission(
        &self,
        id: PermissionId,
    ) -> PermissionPlatformFuture<'_, SystemPermissionObservation>;

    fn prepare_request(&self, id: PermissionId)
        -> PermissionPlatformFuture<'_, RequestInstruction>;

    fn open_system_settings(&self, id: PermissionId) -> PermissionPlatformFuture<'_, ()>;
}

#[cfg(target_os = "windows")]
pub(crate) fn create_adapter(app: tauri::AppHandle) -> Arc<dyn PermissionPlatformAdapter> {
    Arc::new(windows::WindowsPermissionAdapter::new(app))
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn create_adapter(_app: tauri::AppHandle) -> Arc<dyn PermissionPlatformAdapter> {
    Arc::new(UnsupportedPermissionPlatformAdapter)
}

#[cfg(not(target_os = "windows"))]
struct UnsupportedPermissionPlatformAdapter;

#[cfg(not(target_os = "windows"))]
impl PermissionPlatformAdapter for UnsupportedPermissionPlatformAdapter {
    fn query_permission(
        &self,
        _id: PermissionId,
    ) -> PermissionPlatformFuture<'_, SystemPermissionObservation> {
        Box::pin(async {
            Ok(SystemPermissionObservation {
                state: super::model::SystemPermissionState::Unsupported,
                recovery_action: super::model::PermissionRecoveryAction::None,
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
        Box::pin(async {
            Err(PermissionPlatformError {
                code: "permission_platform_unsupported".to_string(),
                message: "system permission management is not available on this platform"
                    .to_string(),
            })
        })
    }
}

#[cfg(test)]
pub(crate) mod fake {
    use std::{
        collections::VecDeque,
        sync::{Arc, Mutex},
    };

    use super::*;

    #[derive(Clone)]
    pub(crate) struct FakePermissionPlatformAdapter {
        observations:
            Arc<Mutex<VecDeque<Result<SystemPermissionObservation, PermissionPlatformError>>>>,
        calls: Arc<Mutex<Vec<(PermissionId, &'static str)>>>,
        instruction: RequestInstruction,
    }

    impl FakePermissionPlatformAdapter {
        pub(crate) fn new(
            observations: impl IntoIterator<
                Item = Result<SystemPermissionObservation, PermissionPlatformError>,
            >,
            instruction: RequestInstruction,
        ) -> Self {
            Self {
                observations: Arc::new(Mutex::new(observations.into_iter().collect())),
                calls: Arc::new(Mutex::new(Vec::new())),
                instruction,
            }
        }

        pub(crate) fn calls(&self) -> Vec<(PermissionId, &'static str)> {
            self.calls.lock().unwrap().clone()
        }
    }

    impl PermissionPlatformAdapter for FakePermissionPlatformAdapter {
        fn query_permission(
            &self,
            id: PermissionId,
        ) -> PermissionPlatformFuture<'_, SystemPermissionObservation> {
            self.calls.lock().unwrap().push((id, "query"));
            let result = self
                .observations
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or_else(|| {
                    Err(PermissionPlatformError {
                        code: "fake_observation_exhausted".to_string(),
                        message: "no fake observation remains".to_string(),
                    })
                });
            Box::pin(async move { result })
        }

        fn prepare_request(
            &self,
            id: PermissionId,
        ) -> PermissionPlatformFuture<'_, RequestInstruction> {
            self.calls.lock().unwrap().push((id, "prepare_request"));
            let instruction = self.instruction.clone();
            Box::pin(async move { Ok(instruction) })
        }

        fn open_system_settings(&self, id: PermissionId) -> PermissionPlatformFuture<'_, ()> {
            self.calls
                .lock()
                .unwrap()
                .push((id, "open_system_settings"));
            Box::pin(async { Ok(()) })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{fake::FakePermissionPlatformAdapter, *};
    use crate::permissions::model::{
        PermissionRecoveryAction, SystemPermissionObservation, SystemPermissionState,
    };

    #[tokio::test]
    async fn fake_adapter_is_deterministic_and_records_calls() {
        let adapter = FakePermissionPlatformAdapter::new(
            [Ok(SystemPermissionObservation {
                state: SystemPermissionState::NotDetermined,
                recovery_action: PermissionRecoveryAction::Request,
            })],
            RequestInstruction::WebMedia,
        );

        assert_eq!(
            adapter
                .query_permission(PermissionId::Microphone)
                .await
                .unwrap()
                .state,
            SystemPermissionState::NotDetermined
        );
        assert_eq!(
            adapter
                .prepare_request(PermissionId::Microphone)
                .await
                .unwrap(),
            RequestInstruction::WebMedia
        );
        adapter
            .open_system_settings(PermissionId::Microphone)
            .await
            .unwrap();
        assert_eq!(
            adapter.calls(),
            [
                (PermissionId::Microphone, "query"),
                (PermissionId::Microphone, "prepare_request"),
                (PermissionId::Microphone, "open_system_settings"),
            ]
        );
    }
}
