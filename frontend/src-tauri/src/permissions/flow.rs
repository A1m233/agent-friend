use std::collections::{BTreeSet, VecDeque};

use super::model::{
    PermissionFlowPhase, PermissionFlowRequest, PermissionFlowSnapshot, PermissionFlowTicket,
    PermissionFlowTicketStatus, PermissionId,
};

#[derive(Default)]
pub(crate) struct PermissionFlowCoordinator {
    active: Option<PermissionFlowSnapshot>,
    pending: VecDeque<PermissionFlowRequest>,
    next_flow_sequence: u64,
}

impl PermissionFlowCoordinator {
    pub(crate) fn active(&self) -> Option<&PermissionFlowSnapshot> {
        self.active.as_ref()
    }

    pub(crate) fn request(
        &mut self,
        request: PermissionFlowRequest,
        target: Vec<PermissionId>,
    ) -> PermissionFlowTicket {
        if target.is_empty() {
            return ticket(request.request_id, None, PermissionFlowTicketStatus::Noop);
        }

        if let Some(active) = self.active.as_mut() {
            if is_subset(&target, &active.permission_ids) {
                if !active.joined_request_ids.contains(&request.request_id) {
                    active.joined_request_ids.push(request.request_id.clone());
                }
                return ticket(
                    request.request_id,
                    Some(active.flow_id.clone()),
                    PermissionFlowTicketStatus::Joined,
                );
            }
            if !self
                .pending
                .iter()
                .any(|pending| pending.request_id == request.request_id)
            {
                self.pending.push_back(request.clone());
            }
            return ticket(request.request_id, None, PermissionFlowTicketStatus::Queued);
        }

        let flow = self.create_flow(&request, target);
        let flow_id = flow.flow_id.clone();
        self.active = Some(flow);
        ticket(
            request.request_id,
            Some(flow_id),
            PermissionFlowTicketStatus::Started,
        )
    }

    pub(crate) fn begin_attempt(
        &mut self,
        flow_id: &str,
        permission_id: PermissionId,
    ) -> Result<(), String> {
        let active = self
            .active
            .as_mut()
            .ok_or_else(|| "no active permission flow".to_string())?;
        if active.flow_id != flow_id {
            return Err("permission flow id does not match the active flow".to_string());
        }
        if active.phase != PermissionFlowPhase::AwaitingUser {
            return Err("permission flow is not awaiting a user attempt".to_string());
        }
        if !active.permission_ids.contains(&permission_id) {
            return Err("permission is not part of the active flow".to_string());
        }
        active.phase = PermissionFlowPhase::Requesting;
        active.current_permission_id = Some(permission_id);
        Ok(())
    }

    pub(crate) fn mark_refreshing(&mut self, flow_id: &str) -> Result<(), String> {
        let active = self
            .active
            .as_mut()
            .ok_or_else(|| "no active permission flow".to_string())?;
        if active.flow_id != flow_id {
            return Err("permission flow id does not match the active flow".to_string());
        }
        active.phase = PermissionFlowPhase::Refreshing;
        Ok(())
    }

    pub(crate) fn abort_attempt(&mut self, flow_id: &str) -> Result<(), String> {
        let active = self
            .active
            .as_mut()
            .ok_or_else(|| "no active permission flow".to_string())?;
        if active.flow_id != flow_id {
            return Err("permission flow id does not match the active flow".to_string());
        }
        if active.phase != PermissionFlowPhase::Requesting {
            return Err("permission flow is not requesting a permission".to_string());
        }
        active.phase = PermissionFlowPhase::AwaitingUser;
        active.current_permission_id = None;
        Ok(())
    }

    pub(crate) fn await_next(&mut self) {
        if let Some(active) = self.active.as_mut() {
            active.phase = PermissionFlowPhase::AwaitingUser;
            active.current_permission_id = None;
        }
    }

    pub(crate) fn finish_active(&mut self) -> Option<PermissionFlowSnapshot> {
        self.active.take()
    }

    pub(crate) fn pop_pending(&mut self) -> Option<PermissionFlowRequest> {
        self.pending.pop_front()
    }

    fn create_flow(
        &mut self,
        request: &PermissionFlowRequest,
        target: Vec<PermissionId>,
    ) -> PermissionFlowSnapshot {
        self.next_flow_sequence += 1;
        PermissionFlowSnapshot {
            flow_id: format!("permission-flow-{}", self.next_flow_sequence),
            phase: PermissionFlowPhase::AwaitingUser,
            permission_ids: deduplicate(target),
            current_permission_id: None,
            joined_request_ids: vec![request.request_id.clone()],
        }
    }
}

fn ticket(
    request_id: String,
    flow_id: Option<String>,
    status: PermissionFlowTicketStatus,
) -> PermissionFlowTicket {
    PermissionFlowTicket {
        request_id,
        flow_id,
        status,
    }
}

fn deduplicate(ids: Vec<PermissionId>) -> Vec<PermissionId> {
    ids.into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn is_subset(candidate: &[PermissionId], superset: &[PermissionId]) -> bool {
    candidate.iter().all(|id| superset.contains(id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::permissions::model::PermissionFlowReason;

    fn request(id: &str) -> PermissionFlowRequest {
        PermissionFlowRequest {
            request_id: id.to_string(),
            reason: PermissionFlowReason::Manual,
            permission_ids: vec![PermissionId::Microphone],
            source_window: None,
        }
    }

    #[test]
    fn joins_matching_targets() {
        let mut coordinator = PermissionFlowCoordinator::default();
        assert_eq!(
            coordinator
                .request(request("one"), vec![PermissionId::Microphone])
                .status,
            PermissionFlowTicketStatus::Started
        );
        assert_eq!(
            coordinator
                .request(request("two"), vec![PermissionId::Microphone])
                .status,
            PermissionFlowTicketStatus::Joined
        );
        assert_eq!(
            coordinator.active().unwrap().joined_request_ids,
            ["one", "two"]
        );

        let queued = PermissionFlowRequest {
            request_id: "three".to_string(),
            permission_ids: Vec::new(),
            ..request("three")
        };
        assert_eq!(
            coordinator
                .request(
                    queued,
                    vec![PermissionId::Microphone, PermissionId::Microphone]
                )
                .status,
            PermissionFlowTicketStatus::Joined,
            "deduplicated equivalent targets still join"
        );
    }

    #[test]
    fn different_targets_are_queued_fifo() {
        let mut coordinator = PermissionFlowCoordinator {
            active: Some(PermissionFlowSnapshot {
                flow_id: "existing".to_string(),
                phase: PermissionFlowPhase::AwaitingUser,
                permission_ids: Vec::new(),
                current_permission_id: None,
                joined_request_ids: vec!["existing-request".to_string()],
            }),
            ..PermissionFlowCoordinator::default()
        };
        assert_eq!(
            coordinator
                .request(request("queued"), vec![PermissionId::Microphone])
                .status,
            PermissionFlowTicketStatus::Queued
        );
        assert_eq!(coordinator.pop_pending().unwrap().request_id, "queued");
    }

    #[test]
    fn attempt_state_is_owned_by_the_coordinator() {
        let mut coordinator = PermissionFlowCoordinator::default();
        let ticket = coordinator.request(request("one"), vec![PermissionId::Microphone]);
        let flow_id = ticket.flow_id.unwrap();
        coordinator
            .begin_attempt(&flow_id, PermissionId::Microphone)
            .unwrap();
        assert_eq!(
            coordinator.active().unwrap().phase,
            PermissionFlowPhase::Requesting
        );
        assert!(coordinator
            .begin_attempt(&flow_id, PermissionId::Microphone)
            .is_err());
        coordinator.abort_attempt(&flow_id).unwrap();
        assert_eq!(
            coordinator.active().unwrap().phase,
            PermissionFlowPhase::AwaitingUser
        );
        coordinator
            .begin_attempt(&flow_id, PermissionId::Microphone)
            .unwrap();
        coordinator.mark_refreshing(&flow_id).unwrap();
        coordinator.await_next();
        assert_eq!(
            coordinator.active().unwrap().phase,
            PermissionFlowPhase::AwaitingUser
        );
    }
}
