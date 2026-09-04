import { Button, StatusDot, TextEllipsis } from "@/components/ui";
import { openSettings } from "@/lib/settings/navigation";
import {
  useCommandRuntimeStore,
  useFilesystemApprovalsStore,
} from "@/stores";
import { CHAT_CONTENT_CONTAINER_CLASS } from "../layout";
import type { PrimaryActivityPresentation } from "./activity/primaryActivity";
import type { PendingApproval, RunningCommand } from "./activity/approvalCandidates";
import { useSessionApprovalModeActions } from "./SessionApprovalModeController";

function PendingApprovalPanel({
  current,
  queuedCount,
  decide,
  enableSessionAuto,
  sessionAutoSubmitting,
}: {
  current: PendingApproval;
  queuedCount: number;
  decide: (approved: boolean) => void;
  enableSessionAuto: () => void;
  sessionAutoSubmitting: boolean;
}) {
  const actionDisabled = current.submitting || sessionAutoSubmitting;
  return (
    <div className="w-full space-y-2 rounded-lg border border-warning/40 bg-bg px-3 py-3 shadow-md">
      <div className="flex items-center gap-2">
        <StatusDot tone="warning" pulse={!current.submitting} />
        <span className="text-xs text-muted">
          {current.kind === "command" ? "本地命令授权" : "本地文件授权"}
        </span>
        {queuedCount > 0 ? <span className="text-xs text-muted">还有 {queuedCount} 项排队</span> : null}
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <Button
            size="xs"
            variant="outline"
            disabled={actionDisabled}
            onClick={() => decide(false)}
          >
            拒绝
          </Button>
          {current.needsSandboxSetup ? (
            <>
              <Button size="xs" variant="outline" disabled>
                本会话无需授权
              </Button>
              <Button size="xs" onClick={() => void openSettings({ target: "permissions" })}>
                前往系统权限
              </Button>
            </>
          ) : (
            <>
              <Button
                size="xs"
                disabled={actionDisabled}
                onClick={() => decide(true)}
              >
                {current.submitting ? "处理中…" : "仅本次允许"}
              </Button>
              <Button
                size="xs"
                variant="destructive"
                disabled={actionDisabled}
                loading={sessionAutoSubmitting}
                onClick={enableSessionAuto}
              >
                本会话无需授权
              </Button>
            </>
          )}
        </div>
      </div>
      <p className="text-sm font-medium text-fg">{current.reason}</p>
      {current.command ? (
        <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface/60 p-2 text-xs text-fg">
          {current.command}
        </pre>
      ) : null}
      <div className="space-y-1 text-xs text-muted">
        {current.riskCode ? (
          <p className="text-warning">风险类别：{current.riskCode}</p>
        ) : null}
        {current.riskMessage ? (
          <p className="text-warning">{current.riskMessage}</p>
        ) : null}
        {current.detailLines.map((line) => (
          <p key={line} className="break-all">{line}</p>
        ))}
      </div>
    </div>
  );
}

function RunningCommandPanel({
  current,
  stop,
}: {
  current: RunningCommand;
  stop: () => void;
}) {
  return (
    <div className="flex w-full items-center gap-3 rounded-lg border border-accent/40 bg-bg px-3 py-2 shadow-md">
      <StatusDot tone="accent" pulse />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs text-muted">
          <span>本地命令运行中</span>
          <span>{current.detail}</span>
        </div>
        <TextEllipsis className="text-sm text-fg">{current.reason}</TextEllipsis>
        <TextEllipsis className="text-xs text-muted">{current.command}</TextEllipsis>
      </div>
      <Button size="xs" variant="outline" onClick={stop}>停止</Button>
    </div>
  );
}

function SessionRunPanel({ label }: { label: string }) {
  return (
    <div className="flex w-full items-center gap-3 rounded-lg border border-border bg-bg px-3 py-2 shadow-md">
      <StatusDot tone="muted" pulse />
      <span className="text-sm text-muted">{label}</span>
    </div>
  );
}

/**
 * The only automatic live command/filesystem action surface in chat.
 * Conversation history stays compact; complete cards appear only after the
 * user explicitly expands a tool row.
 */
export function ApprovalDock({
  activity,
}: {
  activity: PrimaryActivityPresentation;
}) {
  const decideCommand = useCommandRuntimeStore((state) => state.decide);
  const cancelCommand = useCommandRuntimeStore((state) => state.cancel);
  const decideFilesystem = useFilesystemApprovalsStore((state) => state.decide);
  const {
    requestSessionAutoFromApproval,
    submittingRequestId,
  } = useSessionApprovalModeActions();

  const current = activity.surface === "dock" && activity.kind === "approval"
    ? activity.request
    : null;
  const currentRunning = activity.surface === "dock" && activity.kind === "command"
    ? activity.command
    : null;
  const genericLabel = activity.surface === "dock" && activity.kind === "run"
    ? activity.label
    : null;
  if (!current && !currentRunning && !genericLabel) return null;

  const decide = (approved: boolean) => {
    if (!current) return;
    if (current.kind === "command") {
      void decideCommand(current.requestId, approved);
    } else {
      void decideFilesystem(current.requestId, approved);
    }
  };

  return (
    <div
      className="pointer-events-auto w-full pb-3"
      data-primary-activity={activity.kind}
    >
      <div className={CHAT_CONTENT_CONTAINER_CLASS}>
        {current ? (
          <PendingApprovalPanel
            current={current}
            queuedCount={activity.surface === "dock" && activity.kind === "approval"
              ? activity.queuedCount
              : 0}
            decide={decide}
            sessionAutoSubmitting={submittingRequestId === current.requestId}
            enableSessionAuto={() => requestSessionAutoFromApproval({
              kind: current.kind,
              requestId: current.requestId,
              requestVersion: current.requestVersion,
              sessionId: current.sessionId,
              runId: current.runId,
              riskCode: current.riskCode,
            })}
          />
        ) : currentRunning ? (
          <RunningCommandPanel
            current={currentRunning}
            stop={() => void cancelCommand(currentRunning.processId)}
          />
        ) : genericLabel ? (
          <SessionRunPanel label={genericLabel} />
        ) : null}
      </div>
    </div>
  );
}

export {
  buildPendingApprovalQueue,
  buildRunningCommandQueue,
  shouldRenderPendingApprovals,
} from "./activity/approvalCandidates";
