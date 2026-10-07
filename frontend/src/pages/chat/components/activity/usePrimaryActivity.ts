import { useEffect, useMemo } from "react";
import { useShallow } from "zustand/react/shallow";

import {
  useCommandRuntimeStore,
  useConversationStore,
  useFilesystemApprovalsStore,
  useSocialAuthRequestsStore,
} from "@/stores";
import { sessionRunPresentation } from "@/stores/runPresentation";
import {
  buildPendingApprovalQueue,
  buildRunningCommandQueue,
  shouldRenderPendingApprovals,
} from "./approvalCandidates";
import {
  resolvePrimaryActivity,
  socialAuthRequestRefs,
  type PrimaryActivityPresentation,
} from "./primaryActivity";

export function usePrimaryActivity(): PrimaryActivityPresentation {
  const conversation = useConversationStore(useShallow((state) => ({
    currentSessionId: state.currentSessionId,
    messages: state.messages,
    approvalMode: state.approvalMode,
    runSnapshots: state.runSnapshots,
    runCalibrationPhase: state.runCalibrationPhase,
    stopRequestsByRun: state.stopRequestsByRun,
  })));
  const commandRuntime = useCommandRuntimeStore(useShallow((state) => ({
    approvals: state.approvals,
    processes: state.processes,
    submitting: state.submitting,
    runtimeStatus: state.runtimeStatus,
  })));
  const filesystemEntries = useFilesystemApprovalsStore((state) => state.entries);
  const socialAuthEntries = useSocialAuthRequestsStore((state) => state.entries);
  const setTrackedRequestIds = useSocialAuthRequestsStore((state) => state.setTrackedRequestIds);

  const socialAuthRefs = useMemo(
    () => socialAuthRequestRefs(conversation.messages),
    [conversation.messages],
  );
  const socialAuthRequestKey = useMemo(
    () => [...new Set(socialAuthRefs.map((ref) => ref.requestId))].join("\u0000"),
    [socialAuthRefs],
  );

  useEffect(() => {
    setTrackedRequestIds(socialAuthRequestKey ? socialAuthRequestKey.split("\u0000") : []);
  }, [setTrackedRequestIds, socialAuthRequestKey]);

  useEffect(() => () => setTrackedRequestIds([]), [setTrackedRequestIds]);

  return useMemo(() => {
    const runPresentation = sessionRunPresentation(conversation, conversation.currentSessionId);
    const activeRunId = runPresentation.run?.run_id ?? null;
    const pendingApprovals = shouldRenderPendingApprovals(conversation.approvalMode) || Object.values(commandRuntime.approvals).some(r => r.approval_mode_source === "request") || Object.values(filesystemEntries).some(e => e.request.approval_mode_source === "request")
      ? buildPendingApprovalQueue({
          currentSessionId: conversation.currentSessionId,
          activeRunId,
          commandApprovals: commandRuntime.approvals,
          commandSubmitting: commandRuntime.submitting,
          commandRuntimeStatus: commandRuntime.runtimeStatus,
          filesystemEntries,
        })
      : [];
    const runningCommands = buildRunningCommandQueue({
      currentSessionId: conversation.currentSessionId,
      activeRunId,
      commandApprovals: commandRuntime.approvals,
      commandProcesses: commandRuntime.processes,
    });
    const socialAuthSnapshots = Object.fromEntries(socialAuthRefs.map((ref) => [
      ref.requestId,
      socialAuthEntries[ref.requestId]?.snapshot ?? null,
    ]));
    return resolvePrimaryActivity({
      currentSessionId: conversation.currentSessionId,
      runPresentation,
      messages: conversation.messages,
      pendingApprovals,
      runningCommands,
      socialAuthSnapshots,
    });
  }, [
    commandRuntime,
    conversation,
    filesystemEntries,
    socialAuthEntries,
    socialAuthRefs,
  ]);
}
