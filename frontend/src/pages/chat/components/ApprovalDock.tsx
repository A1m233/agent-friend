import { useMemo } from "react";
import { Button, StatusDot } from "@/components/ui";
import { openSettings } from "@/lib/settings/navigation";
import {
  useCommandRuntimeStore,
  useConversationStore,
  useFilesystemApprovalsStore,
} from "@/stores";
import type { CommandApprovalRequest, CommandRuntimeStatus } from "@/services/api/commandRuntime";
import type { FilesystemApprovalEntry } from "@/stores/filesystemApprovals";

type PendingApproval =
  | {
      kind: "command";
      requestId: string;
      reason: string;
      detail: string;
      submitting: boolean;
      needsSandboxSetup: boolean;
    }
  | {
      kind: "filesystem";
      requestId: string;
      reason: string;
      detail: string;
      submitting: boolean;
      needsSandboxSetup: false;
    };

export function buildPendingApprovalQueue(input: {
  currentSessionId: string | null;
  activeRunIdBySession: Record<string, string>;
  commandApprovals: Record<string, CommandApprovalRequest>;
  commandSubmitting: ReadonlySet<string>;
  commandRuntimeStatus: CommandRuntimeStatus | null;
  filesystemEntries: Record<string, FilesystemApprovalEntry>;
}): PendingApproval[] {
  const {
    currentSessionId,
    activeRunIdBySession,
    commandApprovals,
    commandSubmitting,
    commandRuntimeStatus,
    filesystemEntries,
  } = input;
  if (!currentSessionId) return [];
  const activeRunId = activeRunIdBySession[currentSessionId];
  if (!activeRunId) return [];
  const commands: PendingApproval[] = Object.values(commandApprovals)
    .filter((request) => (
      request.status === "pending"
      && request.session_id === currentSessionId
      && request.run_id === activeRunId
    ))
    .map((request) => ({
      kind: "command" as const,
      requestId: request.request_id,
      reason: request.reason,
      detail: request.details.execution === "host_user"
        ? `宿主用户（文件和网络不受限） · ${request.details.cmd}`
        : request.details.cmd,
      submitting: commandSubmitting.has(request.request_id),
      needsSandboxSetup: Boolean(
        request.details.execution === "sandboxed"
        && commandRuntimeStatus?.sandbox_capability.supported
        && !commandRuntimeStatus.sandbox_capability.ready,
      ),
    }));
  const files: PendingApproval[] = Object.values(filesystemEntries)
    .filter((entry) => (
      (entry.status === "pending" || entry.status === "submitting")
      && entry.request.session_id === currentSessionId
      && entry.request.run_id === activeRunId
    ))
    .map((entry) => ({
      kind: "filesystem" as const,
      requestId: entry.request.request_id,
      reason: entry.request.reason,
      detail: [
        ...entry.request.items.map((item) => item.path),
        ...entry.request.actions.map((action) => action.target),
      ].join("、"),
      submitting: entry.status === "submitting",
      needsSandboxSetup: false as const,
    }));
  return [...commands, ...files];
}

/**
 * The only actionable approval surface in chat.
 *
 * Tool cards remain durable history. Keeping the buttons here means a serial
 * sequence of approvals advances through one stable mouse target instead of
 * moving the user between cards of different heights.
 */
export function ApprovalDock() {
  const currentSessionId = useConversationStore((state) => state.currentSessionId);
  const activeRunIdBySession = useConversationStore((state) => state.activeRunIdBySession);
  const commandApprovals = useCommandRuntimeStore((state) => state.approvals);
  const commandSubmitting = useCommandRuntimeStore((state) => state.submitting);
  const commandRuntimeStatus = useCommandRuntimeStore((state) => state.runtimeStatus);
  const decideCommand = useCommandRuntimeStore((state) => state.decide);
  const filesystemEntries = useFilesystemApprovalsStore((state) => state.entries);
  const decideFilesystem = useFilesystemApprovalsStore((state) => state.decide);

  const pending = useMemo(() => {
    return buildPendingApprovalQueue({
      currentSessionId,
      activeRunIdBySession,
      commandApprovals,
      commandSubmitting,
      commandRuntimeStatus,
      filesystemEntries,
    });
  }, [
    activeRunIdBySession,
    commandApprovals,
    commandRuntimeStatus,
    commandSubmitting,
    currentSessionId,
    filesystemEntries,
  ]);

  const current = pending[0];
  if (!current) return null;

  const decide = (approved: boolean) => {
    if (current.kind === "command") {
      void decideCommand(current.requestId, approved);
    } else {
      void decideFilesystem(current.requestId, approved);
    }
  };

  return (
    <div className="pointer-events-auto border-t border-border bg-surface px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-center gap-3 rounded-lg border border-warning/40 bg-bg px-3 py-2 shadow-md">
        <StatusDot tone="warning" pulse={!current.submitting} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-xs text-muted">
            <span>{current.kind === "command" ? "本地命令授权" : "本地文件授权"}</span>
            {pending.length > 1 ? <span>还有 {pending.length - 1} 项排队</span> : null}
          </div>
          <p className="truncate text-sm text-fg">{current.reason}</p>
          <p className="truncate text-xs text-muted">{current.detail}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="xs"
            variant="outline"
            disabled={current.submitting}
            onClick={() => decide(false)}
          >
            拒绝
          </Button>
          {current.needsSandboxSetup ? (
            <Button size="xs" onClick={() => void openSettings({ target: "permissions" })}>
              前往系统权限
            </Button>
          ) : (
            <Button
              size="xs"
              disabled={current.submitting}
              onClick={() => decide(true)}
            >
              {current.submitting ? "处理中…" : "仅本次允许"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
