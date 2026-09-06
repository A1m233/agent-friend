import type { SocialAuthRequestSnapshot } from "@/services";
import type { ChatMessage, ModelResponseProgress, ToolBlock } from "@/types/chat";
import type { SessionRunPresentation } from "@/stores/runPresentation";
import { activeRunActivityMessageId } from "@/stores/runPresentation";
import type { PendingApproval, RunningCommand } from "./approvalCandidates";

const ACTIVE_SOCIAL_AUTH_STATUSES = new Set<SocialAuthRequestSnapshot["status"]>([
  "pending",
  "login_in_progress",
  "authenticated",
  "continuing",
]);

const COMMAND_TOOL_NAMES = new Set(["exec_command", "host_process_start"]);

export interface SocialAuthRequestRef {
  messageId: string;
  toolCallId: string;
  requestId: string;
}

export type MessagePrimaryActivity = Extract<
  PrimaryActivityPresentation,
  { surface: "message" }
>;

export type DockPrimaryActivity = Extract<
  PrimaryActivityPresentation,
  { surface: "dock" }
>;

export type PrimaryActivityPresentation =
  | { surface: "dock"; kind: "approval"; request: PendingApproval; queuedCount: number }
  | { surface: "dock"; kind: "command"; command: RunningCommand }
  | {
      surface: "message";
      kind: "socialAuth";
      messageId: string;
      toolCallId: string;
      requestId: string;
    }
  | {
      surface: "message";
      kind: "compaction";
      messageId: string;
      activityId: string;
    }
  | { surface: "message"; kind: "tool"; messageId: string; toolCallId: string }
  | {
      surface: "message";
      kind: "model";
      messageId: string;
      status: ModelResponseProgress;
    }
  | {
      surface: "message";
      kind: "run";
      messageId: string;
      phase: Exclude<SessionRunPresentation["phase"], "idle">;
      label?: string;
    }
  | {
      surface: "dock";
      kind: "run";
      phase: Exclude<SessionRunPresentation["phase"], "idle">;
      label: string;
    }
  | { surface: "none"; kind: "none" };

export interface PrimaryActivityInput {
  currentSessionId: string | null;
  runPresentation: SessionRunPresentation;
  messages: readonly ChatMessage[];
  pendingApprovals: readonly PendingApproval[];
  runningCommands: readonly RunningCommand[];
  socialAuthSnapshots: Readonly<Record<string, SocialAuthRequestSnapshot | null>>;
}

function parseObject(value: string): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function socialAuthRequestRefs(
  messages: readonly ChatMessage[],
): SocialAuthRequestRef[] {
  const refs: SocialAuthRequestRef[] = [];
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    for (const block of message.blocks) {
      if (block.kind !== "tool" || block.name !== "request_social_auth" || !block.result) continue;
      const requestId = parseObject(block.result).request_id;
      if (typeof requestId !== "string" || !requestId) continue;
      refs.push({ messageId: message.id, toolCallId: block.toolCallId, requestId });
    }
  }
  return refs;
}

function activeSocialAuth(
  input: PrimaryActivityInput,
): SocialAuthRequestRef | null {
  for (const ref of socialAuthRequestRefs(input.messages)) {
    const snapshot = input.socialAuthSnapshots[ref.requestId];
    if (!snapshot) continue;
    if (
      snapshot.sessionId === input.currentSessionId
      && ACTIVE_SOCIAL_AUTH_STATUSES.has(snapshot.status)
    ) return ref;
  }
  return null;
}

function lastRunningCompaction(message: ChatMessage): string | null {
  for (let index = message.blocks.length - 1; index >= 0; index -= 1) {
    const block = message.blocks[index];
    if (block?.kind === "compaction" && block.status === "running") return block.activityId;
  }
  return null;
}

function lastRunningTool(
  message: ChatMessage,
  runningCommand: RunningCommand | undefined,
): ToolBlock | null {
  for (let index = message.blocks.length - 1; index >= 0; index -= 1) {
    const block = message.blocks[index];
    if (block?.kind !== "tool" || block.status !== "running") continue;
    if (block.name === "request_filesystem_access") continue;
    if (runningCommand && COMMAND_TOOL_NAMES.has(block.name)) continue;
    return block;
  }
  return null;
}

function messageRunLabel(
  presentation: SessionRunPresentation,
): string | undefined {
  if (presentation.phase === "stopping") {
    if (presentation.stopPhase === "requesting") return "正在请求停止，等待服务确认…";
    return "正在取消当前任务，任务尚未结束…";
  }
  if (presentation.phase === "queued") return "任务正在等待处理…";
  if (presentation.phase === "preparing") return "正在准备这轮对话…";
  if (presentation.phase === "recovering") return "正在恢复这段对话的运行状态…";
  if (presentation.run?.control_scope !== "desktop_text") {
    return "这段对话正在其他入口处理中…";
  }
  return undefined;
}

function dockRunLabel(presentation: SessionRunPresentation): string {
  return messageRunLabel(presentation) ?? "任务仍在继续…";
}

export function resolvePrimaryActivity(
  input: PrimaryActivityInput,
): PrimaryActivityPresentation {
  const pendingApproval = input.pendingApprovals[0];
  if (pendingApproval) {
    return {
      surface: "dock",
      kind: "approval",
      request: pendingApproval,
      queuedCount: Math.max(0, input.pendingApprovals.length - 1),
    };
  }

  const socialAuth = activeSocialAuth(input);
  if (socialAuth) return { surface: "message", kind: "socialAuth", ...socialAuth };

  const runningCommand = input.runningCommands[0];
  if (runningCommand) {
    return { surface: "dock", kind: "command", command: runningCommand };
  }

  const activeRunId = input.runPresentation.run?.run_id;
  const messageId = activeRunActivityMessageId(input.messages, activeRunId);
  const message = messageId
    ? input.messages.find((candidate) => candidate.id === messageId) ?? null
    : null;
  if (message) {
    const compactionId = lastRunningCompaction(message);
    if (compactionId) {
      return {
        surface: "message",
        kind: "compaction",
        messageId: message.id,
        activityId: compactionId,
      };
    }
    const tool = lastRunningTool(message, runningCommand);
    if (tool) {
      return {
        surface: "message",
        kind: "tool",
        messageId: message.id,
        toolCallId: tool.toolCallId,
      };
    }
    if (message.runStatus && !["queued", "preparing", "stopping"].includes(input.runPresentation.phase)) {
      return {
        surface: "message",
        kind: "model",
        messageId: message.id,
        status: message.runStatus,
      };
    }
  }

  if (!input.runPresentation.busy || input.runPresentation.phase === "idle") {
    return { surface: "none", kind: "none" };
  }
  if (messageId) {
    const label = messageRunLabel(input.runPresentation);
    return {
      surface: "message",
      kind: "run",
      messageId,
      phase: input.runPresentation.phase,
      ...(label ? { label } : {}),
    };
  }
  return {
    surface: "dock",
    kind: "run",
    phase: input.runPresentation.phase,
    label: dockRunLabel(input.runPresentation),
  };
}
