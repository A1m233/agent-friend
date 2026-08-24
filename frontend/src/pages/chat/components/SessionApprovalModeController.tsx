import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ui";
import { sessionsApi } from "@/services";
import { friendlyErrorMessage } from "@/services/http";
import {
  useCommandRuntimeStore,
  useConversationStore,
  useFilesystemApprovalsStore,
} from "@/stores";

export interface SessionAutoApprovalTarget {
  kind: "command" | "filesystem";
  requestId: string;
  sessionId: string;
  runId: string;
  requestVersion: number;
  riskCode?: string | null;
}

type ConfirmationIntent =
  | { source: "composer"; sessionId: string }
  | { source: "approval_card"; target: SessionAutoApprovalTarget };

interface SessionApprovalModeActions {
  requestSessionAutoFromComposer: () => void;
  requestSessionAutoFromApproval: (target: SessionAutoApprovalTarget) => void;
  switchToPerRequest: () => Promise<void>;
  submittingRequestId: string | null;
}

const SessionApprovalModeContext = createContext<SessionApprovalModeActions | null>(null);

export function useSessionApprovalModeActions(): SessionApprovalModeActions {
  const value = useContext(SessionApprovalModeContext);
  if (!value) throw new Error("SessionApprovalModeController is missing");
  return value;
}

export function SessionApprovalModeController({ children }: { children: ReactNode }) {
  const currentSessionId = useConversationStore((state) => state.currentSessionId);
  const setApprovalMode = useConversationStore((state) => state.setApprovalMode);
  const applyApprovalModeState = useConversationStore((state) => state.applyApprovalModeState);
  const commandApprovals = useCommandRuntimeStore((state) => state.approvals);
  const refreshCommands = useCommandRuntimeStore((state) => state.refresh);
  const filesystemEntries = useFilesystemApprovalsStore((state) => state.entries);
  const refreshFilesystem = useFilesystemApprovalsStore((state) => state.refresh);
  const [intent, setIntent] = useState<ConfirmationIntent | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setIntent(null);
    setSubmitting(false);
  }, [currentSessionId]);

  useEffect(() => {
    if (intent?.source !== "approval_card") return;
    const target = intent.target;
    const current = target.kind === "command"
      ? commandApprovals[target.requestId]
      : filesystemEntries[target.requestId]?.request;
    if (
      !current
      || current.status !== "pending"
      || current.session_id !== target.sessionId
      || current.run_id !== target.runId
      || current.version !== target.requestVersion
    ) {
      setIntent(null);
      setSubmitting(false);
    }
  }, [commandApprovals, filesystemEntries, intent]);

  const requestSessionAutoFromComposer = useCallback(() => {
    if (!currentSessionId) return;
    setIntent({ source: "composer", sessionId: currentSessionId });
  }, [currentSessionId]);

  const requestSessionAutoFromApproval = useCallback((target: SessionAutoApprovalTarget) => {
    if (target.sessionId !== currentSessionId) return;
    setIntent({ source: "approval_card", target });
  }, [currentSessionId]);

  const switchToPerRequest = useCallback(async () => {
    const result = await setApprovalMode("per_request", false);
    if (result.status === "applied") {
      toast.success("已恢复每次授权");
    } else if (result.status === "failed") {
      toast.error("授权模式没有切换", { description: result.error.friendly });
    }
  }, [setApprovalMode]);

  const confirm = useCallback(async () => {
    if (!intent || submitting) return;
    setSubmitting(true);
    try {
      if (intent.source === "composer") {
        const result = await setApprovalMode("session_auto", true);
        if (result.status === "failed") {
          toast.error("授权模式没有切换", { description: result.error.friendly });
          return;
        }
        if (result.status === "stale") return;
      } else {
        const target = intent.target;
        const state = await sessionsApi.enableSessionAutoForApproval({
          requestId: target.requestId,
          sessionId: target.sessionId,
          runId: target.runId,
          requestVersion: target.requestVersion,
          modeVersion: useConversationStore.getState().approvalModeVersion,
        });
        if (useConversationStore.getState().currentSessionId !== target.sessionId) return;
        applyApprovalModeState(state);
        await Promise.allSettled([refreshCommands(), refreshFilesystem()]);
      }
      setIntent(null);
      toast.warning("本会话已开启无需授权", {
        description: "建议完成连续操作后恢复“每次授权”。",
      });
    } catch (error) {
      if (currentSessionId) {
        await useConversationStore.getState().refreshApprovalMode(currentSessionId);
      }
      await Promise.allSettled([refreshCommands(), refreshFilesystem()]);
      toast.error("没有开启本会话免授权", {
        description: friendlyErrorMessage(error),
      });
    } finally {
      setSubmitting(false);
    }
  }, [
    applyApprovalModeState,
    currentSessionId,
    intent,
    refreshCommands,
    refreshFilesystem,
    setApprovalMode,
    submitting,
  ]);

  const value = useMemo<SessionApprovalModeActions>(() => ({
    requestSessionAutoFromComposer,
    requestSessionAutoFromApproval,
    switchToPerRequest,
    submittingRequestId:
      submitting && intent?.source === "approval_card" ? intent.target.requestId : null,
  }), [
    intent,
    requestSessionAutoFromApproval,
    requestSessionAutoFromComposer,
    submitting,
    switchToPerRequest,
  ]);

  const riskCode = intent?.source === "approval_card" ? intent.target.riskCode : null;

  return (
    <SessionApprovalModeContext.Provider value={value}>
      {children}
      <ConfirmDialog
        open={intent !== null}
        title="确认在本会话中免除后续授权？"
        description="这是高风险设置。建议继续使用“每次授权”，只批准你正在检查的单个操作。"
        confirmLabel="我已了解风险，仍要开启"
        cancelLabel="继续每次授权"
        variant="destructive"
        loading={submitting}
        onCancel={() => {
          if (!submitting) setIntent(null);
        }}
        onConfirm={() => void confirm()}
      >
        <div className="space-y-3 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm text-fg">
          <div className="flex items-start gap-2 font-medium text-warning">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>后续普通请求将不再逐项询问</span>
          </div>
          <div className="space-y-2 text-muted">
            <p>
              Agent 可以读取、创建、修改、覆盖、移动或删除本地文件，并运行本地命令和使用申请到的网络能力。
            </p>
            <p>
              这也包括 Agent 显式申请的宿主用户命令；这类命令不受文件系统和网络沙箱限制。
            </p>
            <p>
              系统仍可能拒绝可识别的灾难性动作，但无法保证识别任意脚本内部的危险行为。
            </p>
            {riskCode ? <p className="text-warning">当前请求风险类别：{riskCode}</p> : null}
          </div>
        </div>
      </ConfirmDialog>
    </SessionApprovalModeContext.Provider>
  );
}
