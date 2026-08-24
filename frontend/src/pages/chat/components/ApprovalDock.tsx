import { useMemo } from "react";
import { Button, StatusDot, TextEllipsis } from "@/components/ui";
import { openSettings } from "@/lib/settings/navigation";
import {
  useCommandRuntimeStore,
  useConversationStore,
  useFilesystemApprovalsStore,
} from "@/stores";
import type {
  CommandApprovalRequest,
  CommandProcessSnapshot,
  CommandRuntimeStatus,
} from "@/services/api/commandRuntime";
import type { FilesystemApprovalEntry } from "@/stores/filesystemApprovals";
import { CHAT_CONTENT_CONTAINER_CLASS } from "../layout";
import { useSessionApprovalModeActions } from "./SessionApprovalModeController";

type PendingApproval = {
  kind: "command" | "filesystem";
  requestId: string;
  requestVersion: number;
  sessionId: string;
  runId: string;
  reason: string;
  command: string | null;
  detailLines: string[];
  submitting: boolean;
  needsSandboxSetup: boolean;
  riskCode: string | null;
};

interface RunningCommand {
  processId: string;
  reason: string;
  command: string;
  detail: string;
}

function commandExecutionLabel(request: CommandApprovalRequest): string {
  return request.details.execution === "host_user"
    ? "宿主用户（文件和网络不受限）"
    : "Codex 沙箱";
}

function commandFilesystemLabel(request: CommandApprovalRequest): string {
  if (request.details.execution === "host_user") return "不受限";
  return request.details.filesystem_access === "read_write" ? "可写" : "只读";
}

function commandNetworkLabel(request: CommandApprovalRequest): string {
  if (request.details.execution === "host_user") return "不受限";
  return request.details.network_access === "enabled" ? "允许" : "禁止";
}

function commandDetailLines(request: CommandApprovalRequest): string[] {
  const details = request.details;
  const lines = [
    `工作目录：${details.workdir}`,
    `模式：${details.mode === "streaming" ? "交互式" : "普通"} · 超时 ${details.timeout_seconds} 秒`,
    `执行级别：${commandExecutionLabel(request)}`,
    `文件：${commandFilesystemLabel(request)} · 网络：${commandNetworkLabel(request)}`,
  ];
  if (details.skill) {
    lines.push(`Skill：${details.skill.skill_id} · ${details.skill.entry}`);
  }
  if (details.execution_environment) {
    const environment = details.execution_environment;
    const provider = environment.provider === "python"
      ? `产品 Python ${environment.runtime_version}`
      : `${environment.provider} ${environment.runtime_version}`;
    lines.push(
      `执行环境：${provider} · 当前会话隔离环境`
      + (environment.package_operation ? " · 依赖操作" : ""),
    );
  }
  for (const root of details.additional_roots) {
    lines.push(`附加范围：${root.path}（${root.access === "read_write" ? "可写" : "只读"}）`);
  }
  for (const root of details.host_derived_roots) {
    lines.push(`宿主派生程序范围：${root.path}（只读）`);
  }
  return lines;
}

const FILESYSTEM_PERMISSION_LABELS = {
  search_metadata: "查找与查看元数据",
  read_text: "读取文本",
  create: "创建",
  edit: "修改",
} as const;

const FILESYSTEM_ACTION_LABELS = {
  overwrite: "覆盖",
  move: "移动",
  delete: "删除",
} as const;

function filesystemDetailLines(entry: FilesystemApprovalEntry): string[] {
  return [
    ...entry.request.items.map((item) => (
      `范围：${item.path}（${item.permissions.map((permission) => (
        FILESYSTEM_PERMISSION_LABELS[permission]
      )).join("、")}）`
    )),
    ...entry.request.actions.map((action) => (
      `操作：${FILESYSTEM_ACTION_LABELS[action.action]} ${action.target}`
      + (action.recursive ? "（包含子目录）" : "")
    )),
  ];
}

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
      requestVersion: request.version,
      sessionId: request.session_id,
      runId: request.run_id,
      reason: request.reason,
      command: request.details.cmd,
      detailLines: commandDetailLines(request),
      submitting: commandSubmitting.has(request.request_id),
      needsSandboxSetup: Boolean(
        request.details.execution === "sandboxed"
        && commandRuntimeStatus?.sandbox_capability.supported
        && !commandRuntimeStatus.sandbox_capability.ready,
      ),
      riskCode: request.risk?.code ?? null,
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
      requestVersion: entry.request.version,
      sessionId: entry.request.session_id,
      runId: entry.request.run_id,
      reason: entry.request.reason,
      command: null,
      detailLines: filesystemDetailLines(entry),
      submitting: entry.status === "submitting",
      needsSandboxSetup: false as const,
      riskCode: entry.request.risk?.code ?? null,
    }));
  return [...commands, ...files];
}

export function buildRunningCommandQueue(input: {
  currentSessionId: string | null;
  activeRunIdBySession: Record<string, string>;
  commandApprovals: Record<string, CommandApprovalRequest>;
  commandProcesses: Record<string, CommandProcessSnapshot>;
}): RunningCommand[] {
  const {
    currentSessionId,
    activeRunIdBySession,
    commandApprovals,
    commandProcesses,
  } = input;
  if (!currentSessionId) return [];
  const activeRunId = activeRunIdBySession[currentSessionId];
  if (!activeRunId) return [];
  return Object.values(commandProcesses)
    .filter((process) => (
      process.status === "running"
      && process.session_id === currentSessionId
      && process.run_id === activeRunId
    ))
    .sort((left, right) => left.started_at.localeCompare(right.started_at))
    .map((process) => {
      const approval = Object.values(commandApprovals).find((request) => (
        request.tool_call_id === process.tool_call_id
      ));
      const duration = process.duration_seconds == null
        ? ""
        : ` · 已运行 ${process.duration_seconds.toFixed(1)} 秒`;
      return {
        processId: process.process_id,
        reason: approval?.reason || "运行本地命令",
        command: approval?.details.cmd || "命令详情可在对话中展开查看",
        detail: `${process.mode === "streaming" ? "交互式" : "普通"}${duration}`,
      };
    });
}

function PendingApprovalPanel({
  current,
  queuedCount,
  decide,
  enableSessionAuto,
  showSessionAuto,
  sessionAutoSubmitting,
}: {
  current: PendingApproval;
  queuedCount: number;
  decide: (approved: boolean) => void;
  enableSessionAuto: () => void;
  showSessionAuto: boolean;
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
              {showSessionAuto ? (
                <Button size="xs" variant="outline" disabled>
                  本会话无需授权
                </Button>
              ) : null}
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
              {showSessionAuto ? (
                <Button
                  size="xs"
                  variant="destructive"
                  disabled={actionDisabled}
                  loading={sessionAutoSubmitting}
                  onClick={enableSessionAuto}
                >
                  本会话无需授权
                </Button>
              ) : null}
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

/**
 * The only automatic live command/filesystem action surface in chat.
 * Conversation history stays compact; complete cards appear only after the
 * user explicitly expands a tool row.
 */
export function ApprovalDock() {
  const currentSessionId = useConversationStore((state) => state.currentSessionId);
  const activeRunIdBySession = useConversationStore((state) => state.activeRunIdBySession);
  const commandApprovals = useCommandRuntimeStore((state) => state.approvals);
  const commandProcesses = useCommandRuntimeStore((state) => state.processes);
  const commandSubmitting = useCommandRuntimeStore((state) => state.submitting);
  const commandRuntimeStatus = useCommandRuntimeStore((state) => state.runtimeStatus);
  const decideCommand = useCommandRuntimeStore((state) => state.decide);
  const cancelCommand = useCommandRuntimeStore((state) => state.cancel);
  const filesystemEntries = useFilesystemApprovalsStore((state) => state.entries);
  const decideFilesystem = useFilesystemApprovalsStore((state) => state.decide);
  const approvalMode = useConversationStore((state) => state.approvalMode);
  const {
    requestSessionAutoFromApproval,
    submittingRequestId,
  } = useSessionApprovalModeActions();

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

  const running = useMemo(() => buildRunningCommandQueue({
    currentSessionId,
    activeRunIdBySession,
    commandApprovals,
    commandProcesses,
  }), [
    activeRunIdBySession,
    commandApprovals,
    commandProcesses,
    currentSessionId,
  ]);

  const current = pending[0];
  const currentRunning = current ? null : running[0];
  if (!current && !currentRunning) return null;

  const decide = (approved: boolean) => {
    if (!current) return;
    if (current.kind === "command") {
      void decideCommand(current.requestId, approved);
    } else {
      void decideFilesystem(current.requestId, approved);
    }
  };

  return (
    <div className="pointer-events-auto w-full pb-3">
      <div className={CHAT_CONTENT_CONTAINER_CLASS}>
        {current ? (
          <PendingApprovalPanel
            current={current}
            queuedCount={pending.length - 1}
            decide={decide}
            showSessionAuto={approvalMode === "per_request"}
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
        ) : null}
      </div>
    </div>
  );
}
