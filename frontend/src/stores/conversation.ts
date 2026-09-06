/**
 * 当前会话 store（design §4.3/§3.5）：消息列表 + 流式态，消费 AG-UI 事件流。
 *
 * - `send`：乐观插入 user + 占位 assistant；桌面由 run_event、Web 由 fetch-SSE 投影增量。
 * - `openSession`：拉历史事件投影成消息，续聊用同一 session_id 作 threadId。
 * - 错误统一为 turn 状态，不暴露技术细节。
 */

import { create } from "zustand";
import { EventType, type BaseEvent } from "@ag-ui/core";
import { setLastChatSessionId } from "@/lib/persistence/chatUi";
import { isTauri } from "@/utils/tauri";
import {
  credentialGroupForModel,
  defaultModelRuntimeConfig,
  modelOption,
  resolveDefaultConversationRuntimeConfig,
  type ModelRuntimeConfig,
} from "@/lib/model-config";
import {
  continueToolResultStream,
  editResendLatestStream,
  modelsApi,
  runAgentStream,
  runsApi,
  sessionsApi,
  StreamAdmissionError,
} from "@/services";
import type {
  AttachmentDraft,
  EditResendAttachmentInput,
  FriendlyError,
} from "@/services";
import type {
  ChatAttachment,
  ChatMessage,
  ChatTextReference,
  TextBlock,
} from "@/types/chat";
import type { SessionDetail } from "@/types/meta";
import type {
  SessionApprovalMode,
  SessionApprovalModeState,
} from "@/services/api/sessions";
import type { PushEvent } from "@/types/push";
import type { RunOutcome, RunSnapshot } from "@/types/run";
import type { SearchNavigationTarget, SessionSearchResult } from "@/types/search";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { useSessionsStore } from "./sessions";
import {
  editDraftKey,
  HOME_DRAFT_KEY,
  type DraftAttachmentState,
  useDraftStore,
} from "./drafts";
import { createStreamUpdateScheduler } from "./streamUpdateScheduler";
import { createSessionDetailReads } from "./sessionDetailReads";
import { createRunCancellation, type StopRequestPhase } from "./runCancellation";
import {
  createApprovalModeRefresher,
  type ApprovalModeRefreshOptions,
} from "./approvalModeRefresh";
import { useRuntimeCredentialStore } from "./runtimeCredentials";
import {
  isSessionBusy,
  sessionActiveRun,
  sessionRunPresentation,
  type RunCalibrationPhase,
} from "./runPresentation";
import {
  canReconcileSnapshotAbsence,
  emptySnapshotFence,
  pruneSnapshotFence,
  touchSnapshotKey,
  type SnapshotFenceState,
} from "./snapshotFence";

/** 前端本地网络/HTTP 失败兜底；RUN_ERROR 用 bridge 按错误码清洗后的文案。 */
const FRIENDLY_FALLBACK = "这轮回答中断了，没有生成完整回复。请稍后重试。";

function withObservedTerminalTimestamp(event: BaseEvent): BaseEvent {
  if (event.type !== EventType.RUN_FINISHED && event.type !== EventType.RUN_ERROR) return event;
  if (typeof event.timestamp === "number" && Number.isFinite(event.timestamp)) return event;
  return { ...event, timestamp: Date.now() };
}

function withObservedPushTerminalTimestamp(event: PushEvent): PushEvent {
  if (event.type !== "done") return event;
  if (typeof event.timestamp === "number" && Number.isFinite(event.timestamp)) return event;
  return { ...event, timestamp: Date.now() };
}

function markMessageTerminal(
  message: ChatMessage,
  status: Exclude<ChatMessage["status"], "streaming">,
  updates: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    ...message,
    ...updates,
    status,
    createdAt: new Date().toISOString(),
  };
}

export function runtimeCredentialGroupForModel(
  model: string,
): ReturnType<typeof credentialGroupForModel> {
  return credentialGroupForModel(model);
}

interface RunTransport {
  sessionId: string;
  controller: AbortController;
}

const activeTransports = new Map<string, RunTransport>();
const pendingEditAdmissionBySession = new Map<string, AbortController>();
const HOME_VIEW_KEY = "__agent_friend_home__";
const MAX_IDLE_SESSION_VIEWS = 20;
const MAX_RUN_SNAPSHOTS = 512;
let openSessionToken = 0;
let detailRefreshSeq = 0;
let runtimeGeneration = 0;
const detailReads = createSessionDetailReads(
  (sessionId) => sessionsApi.get(sessionId),
  (sessionId) => { void useConversationStore.getState().refreshCurrentSession(sessionId); },
);
let appliedDetails = new WeakSet<SessionDetail>();
interface PendingInput {
  sessionId: string;
  runtimeId: string | null;
  userMessageId: string;
  assistantId: string;
}
const pendingInputs = new Map<string, PendingInput>();
let modelMutationSeq = 0;
let approvalModeMutationSeq = 0;

function findRunAssistantIndex(messages: readonly ChatMessage[], runId: string): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role === "assistant" && message.runId === runId) return index;
  }
  return -1;
}
let searchNavigationToken = 0;
let runCalibrationSeq = 0;

type OpenSessionFailureMode = "message" | "home" | "previous";

interface OpenSessionOptions {
  /** 自动恢复失败时用 home，手动打开失败时沿用 message。 */
  failureMode?: OpenSessionFailureMode;
  /** 自动恢复读取的是已有持久化指针，不需要重复写入。 */
  remember?: boolean;
  /** 归档搜索结果允许作为不在普通侧栏列表中的当前会话查看。 */
  detached?: boolean;
  /** 搜索跳转在历史加载期间就保持可见，用于抑制首次自动滚底。 */
  searchTarget?: SearchNavigationTarget;
}

export interface EditingMessageDescriptor {
  sessionId: string;
  messageId: string;
  draftKey: string;
  originalText: string;
  originalAttachments: ChatAttachment[];
  originalReferences?: ChatTextReference[];
  phase: "editing" | "submitting";
}

export interface ConversationState {
  currentSessionId: string | null;
  currentModel: string | null;
  currentReasoningPreset: string | null;
  approvalMode: SessionApprovalMode;
  approvalModeVersion: number;
  approvalModeChanging: boolean;
  modelSwitching: boolean;
  modelSwitchTarget: string | null;
  editingMessage: EditingMessageDescriptor | null;
  messages: ChatMessage[];
  historyLoading: boolean;
  /** 历史会话加载完成事件序号；UI 用它触发一次 post-layout 滚动校正。 */
  historyLoadSeq: number;
  /** 后台同步完成序号；UI 用它保护阅读锚点，不触发历史首次加载滚底。 */
  syncRefreshSeq: number;
  sessionViews: Record<string, SessionConversationView>;
  runSnapshots: Record<string, RunSnapshot>;
  runtimeInstanceId: string | null;
  runCalibrationPhase: RunCalibrationPhase;
  runFence: SnapshotFenceState;
  viewAccessedAt: Record<string, number>;
  stopRequestsByRun: Record<string, StopRequestPhase>;
  unreadRunOutcomeBySession: Record<string, "completed" | "failed">;
  detachedSessionId: string | null;
  searchTarget: SearchNavigationTarget | null;
  /** 开一个新会话（清空当前视图；session 在首次 send 时由 bridge 自动创建）。 */
  newSession: () => void;
  resolveNewConversationDraftRuntimeConfig: () => void;
  /** 切换到历史会话并加载其上下文。 */
  openSession: (sessionId: string, options?: OpenSessionOptions) => Promise<boolean>;
  openSearchResult: (result: SessionSearchResult, query: string) => Promise<boolean>;
  clearSearchTarget: (token: number) => void;
  refreshCurrentSession: (sessionId: string) => Promise<"applied" | "stale" | "failed">;
  invalidateBackgroundRefreshes: () => void;
  invalidateSessionDetail: (sessionId: string) => void;
  observeRuntimeIdentity: (runtimeId: string) => void;
  applyRunEvents: (sessionId: string, runId: string, events: PushEvent[]) => void;
  applyRunSnapshot: (snapshot: RunSnapshot) => void;
  calibrateRuns: () => Promise<void>;
  markSessionViewed: (sessionId: string) => void;
  pruneCaches: () => void;
  selectModel: (modelId: string) => Promise<ModelSelectionOutcome>;
  selectRuntimeConfig: (config: ModelRuntimeConfig) => Promise<ModelSelectionOutcome>;
  setApprovalMode: (
    mode: SessionApprovalMode,
    riskAcknowledged: boolean,
  ) => Promise<ApprovalModeSelectionOutcome>;
  applyApprovalModeState: (state: SessionApprovalModeState) => void;
  refreshApprovalMode: (
    sessionId: string,
    options?: ApprovalModeRefreshOptions,
  ) => Promise<"applied" | "stale" | "failed">;
  beginEditingMessage: (messageId: string) => boolean;
  cancelEditing: () => void;
  /** 接纳一条消息并启动流式回复；接纳结果与整轮完成结果分开返回。 */
  send: (
    text: string,
    attachments?: AttachmentDraft[],
    references?: ChatTextReference[],
  ) => SendStartResult;
  /** 编辑并重发最后一条用户消息。 */
  editResendLatest: (
    text: string,
    attachments: EditResendAttachmentInput[],
    optimisticAttachments: ChatAttachment[],
    references?: ChatTextReference[],
  ) => Promise<SendOutcome>;
  /** 不重跑工具，复用失败轮中已经完成的工具结果继续生成。 */
  continueToolResult: (messageId: string) => Promise<void>;
  /** 请求打断当前 run；终态仍由 Runtime snapshot 确认。 */
  stop: () => Promise<StopOutcome>;
}

interface SessionConversationView {
  messages: ChatMessage[];
  currentModel: string | null;
  currentReasoningPreset: string | null;
  approvalMode: SessionApprovalMode;
  approvalModeVersion: number;
  historyLoading: boolean;
  historyLoadSeq: number;
  syncRefreshSeq: number;
  revision: number;
}

export type SendOutcome = "completed" | "failed" | "interrupted" | "rejected";

export type { StopRequestPhase } from "./runCancellation";

export type StopOutcome = "accepted" | "pending" | "already-pending" | "noop" | "failed";

export type SendStartResult =
  | {
      accepted: false;
      reason:
        | "empty"
        | "busy"
        | "history-loading"
        | "model-switching"
        | "model-unavailable"
        | "editing";
    }
  | {
      accepted: true;
      sessionId: string;
      runId: string;
      completion: Promise<SendOutcome>;
    };

export type ModelSelectionOutcome =
  | { status: "applied"; model: string; reasoningPreset: string }
  | { status: "stale" }
  | { status: "failed"; error: FriendlyError };

export type ApprovalModeSelectionOutcome =
  | { status: "applied"; mode: SessionApprovalMode; version: number }
  | { status: "stale" }
  | { status: "failed"; error: FriendlyError };

function asFriendlyError(error: unknown): FriendlyError {
  if (error && typeof error === "object" && "friendly" in error) {
    const friendly = (error as { friendly?: unknown }).friendly;
    const code = (error as { code?: unknown }).code;
    if (typeof friendly === "string") {
      return { friendly, code: typeof code === "string" ? code : undefined };
    }
  }
  return { friendly: "模型没能切换成功，稍后再试试？", code: "unknown" };
}

function asApprovalModeFriendlyError(error: unknown): FriendlyError {
  if (error && typeof error === "object" && "friendly" in error) {
    const friendly = (error as { friendly?: unknown }).friendly;
    const code = (error as { code?: unknown }).code;
    if (typeof friendly === "string") {
      return { friendly, code: typeof code === "string" ? code : undefined };
    }
  }
  return { friendly: "授权模式没有切换，请稍后重试。", code: "unknown" };
}

function newId(): string {
  return crypto.randomUUID();
}

function optimisticRunSnapshot(
  runId: string,
  sessionId: string,
  createdAt: string,
): RunSnapshot {
  return {
    run_id: runId,
    session_id: sessionId,
    status: "queued",
    outcome: null,
    reason_code: "",
    channel: "text",
    origin: "ag_ui",
    control_scope: "desktop_text",
    created_at: createdAt,
    started_at: null,
    finished_at: null,
    version: 0,
    input_accepted: null,
  };
}

function userMessage(
  text: string,
  createdAt = new Date().toISOString(),
  attachments: ChatAttachment[] = [],
  id = newId(),
  references: ChatTextReference[] = [],
): ChatMessage {
  const block: TextBlock = { kind: "text", mid: id, text };
  return {
    id,
    role: "user",
    createdAt,
    blocks: text ? [block] : [],
    attachments,
    references,
    status: "complete",
  };
}

function assistantErrorMessage(error: string): ChatMessage {
  return {
    id: newId(),
    role: "assistant",
    createdAt: new Date().toISOString(),
    blocks: [],
    status: "error",
    error,
  };
}

function messageText(message: ChatMessage): string {
  return message.blocks
    .filter((block) => block.kind === "text")
    .map((block) => (block.kind === "text" ? block.text : ""))
    .join("\n\n");
}

function projectSessionDetail(detail: SessionDetail) {
  const events = detail.active_events ?? detail.events;
  if (!events) throw new Error("session detail is missing events");
  return projectSessionEvents(events);
}

export function messageHasConversationContent(message: ChatMessage): boolean {
  return Boolean(message.error) || message.blocks.some((block) => {
    if (block.kind === "text" || block.kind === "thinking") return Boolean(block.text.trim());
    return true;
  });
}

function terminalStatusForOutcome(outcome: RunOutcome | null): ChatMessage["status"] {
  if (outcome === "completed") return "complete";
  if (outcome === "failed") return "error";
  return "interrupted";
}

function terminalizeRunMessage(
  message: ChatMessage,
  snapshot: RunSnapshot,
): ChatMessage {
  if (message.role !== "assistant" || message.runId !== snapshot.run_id) return message;
  const outcome = snapshot.outcome;
  if (message.status !== "streaming") {
    return {
      ...message,
      runOutcome: outcome ?? message.runOutcome,
      runStartedAt: snapshot.started_at ?? message.runStartedAt,
      runFinishedAt: snapshot.finished_at ?? undefined,
      runStatus: undefined,
    };
  }
  const status = terminalStatusForOutcome(outcome);
  return {
    ...message,
    status,
    runOutcome: outcome ?? undefined,
    runStartedAt: snapshot.started_at ?? message.runStartedAt,
    runFinishedAt: snapshot.finished_at ?? undefined,
    createdAt: snapshot.finished_at ?? message.createdAt,
    ...(status === "error" && !message.error
      ? { error: FRIENDLY_FALLBACK, errorCode: snapshot.reason_code || "runtime_error" }
      : {}),
    runStatus: undefined,
  };
}

function terminalizeRunMessages(
  messages: ChatMessage[],
  snapshot: RunSnapshot,
): ChatMessage[] {
  let changed = false;
  const next = messages.map((message) => {
    const terminal = terminalizeRunMessage(message, snapshot);
    if (terminal !== message) changed = true;
    return terminal;
  });
  return changed ? next : messages;
}

function hasVisibleRunMessage(messages: ChatMessage[], runId: string): boolean {
  return messages.some((message) => (
    message.role === "assistant"
    && message.runId === runId
    && messageHasConversationContent(message)
  ));
}

async function markCompletedRunUnread(snapshot: RunSnapshot): Promise<void> {
  if (snapshot.outcome !== "completed") return;
  const initial = useConversationStore.getState();
  const epoch = runtimeGeneration;
  if (initial.currentSessionId === snapshot.session_id) return;
  let messages = initial.sessionViews[snapshot.session_id]?.messages;
  if (!messages || !hasVisibleRunMessage(messages, snapshot.run_id)) {
    try {
      messages = projectSessionDetail(await detailReads.read(snapshot.session_id));
    } catch {
      return;
    }
  }
  const state = useConversationStore.getState();
  const latest = state.runSnapshots[snapshot.run_id];
  if (
    state.currentSessionId === snapshot.session_id
    || epoch !== runtimeGeneration
    || latest?.status !== "terminal"
    || latest.version !== snapshot.version
    || !hasVisibleRunMessage(messages, snapshot.run_id)
  ) return;
  useConversationStore.setState((current) => ({
    unreadRunOutcomeBySession: {
      ...current.unreadRunOutcomeBySession,
      [snapshot.session_id]: "completed",
    },
  }));
}

function dropOptimisticRun(runId: string): void {
  cancellations.forget(runId);
  useConversationStore.setState((state) => {
    if (state.runSnapshots[runId]?.version !== 0) return state;
    const runSnapshots = { ...state.runSnapshots };
    const stopRequestsByRun = { ...state.stopRequestsByRun };
    delete runSnapshots[runId];
    delete stopRequestsByRun[runId];
    return {
      runSnapshots,
      stopRequestsByRun,
      runFence: pruneSnapshotFence(state.runFence, new Set(Object.keys(runSnapshots))),
    };
  });
}

function settleSubmittedInput(runId: string, restore: boolean): void {
  const pending = pendingInputs.get(runId);
  if (!pending) return;
  pendingInputs.delete(runId);
  useDraftStore.getState().finishSubmission(runId, restore);
  if (!restore) return;
  useConversationStore.setState((state) => {
    const view = state.sessionViews[pending.sessionId];
    if (!view) return state;
    const messages = view.messages.filter((message) => message.id !== pending.userMessageId
      && (message.id !== pending.assistantId || messageHasConversationContent(message)));
    return {
      sessionViews: { ...state.sessionViews, [pending.sessionId]: { ...view, messages } },
      ...(state.currentSessionId === pending.sessionId ? { messages } : {}),
    };
  });
}

function observeInputAcceptance(snapshot: RunSnapshot): void {
  const pending = pendingInputs.get(snapshot.run_id);
  if (!pending || pending.sessionId !== snapshot.session_id) return;
  if (pending.runtimeId && snapshot.runtime_instance_id && pending.runtimeId !== snapshot.runtime_instance_id) return;
  if (snapshot.runtime_instance_id) pending.runtimeId = snapshot.runtime_instance_id;
  if (snapshot.input_accepted === true) settleSubmittedInput(snapshot.run_id, false);
  else if (snapshot.status === "terminal") {
    settleSubmittedInput(snapshot.run_id, snapshot.input_accepted === false || snapshot.outcome === "rejected");
  }
}

const cancellations = createRunCancellation({
  cancel: (sessionId, runId, runtimeId) => runtimeId
    ? runsApi.cancel(sessionId, runId, runtimeId) : runsApi.cancel(sessionId, runId),
  snapshot: (snapshot) => useConversationStore.getState().applyRunSnapshot(snapshot),
  accepted: (sessionId, runId) => {
    const transport = activeTransports.get(runId);
    if (transport?.sessionId === sessionId) transport.controller.abort();
  },
  changed: (stopRequestsByRun) => useConversationStore.setState({ stopRequestsByRun }),
  runtimeMismatch: () => { void useConversationStore.getState().calibrateRuns(); },
});

function observeSendLifecycle(event: BaseEvent, sessionId: string, runId: string): void {
  const fields = event as unknown as Record<string, unknown>;
  if (fields.threadId !== sessionId || fields.runId !== runId) return;
  const result = fields.result && typeof fields.result === "object"
    ? fields.result as Record<string, unknown> : fields;
  const inputAccepted = result.inputAccepted ?? fields.inputAccepted;
  const runtimeId = typeof fields.runtimeInstanceId === "string" ? fields.runtimeInstanceId : null;
  const state = useConversationStore.getState();
  const previous = state.runSnapshots[runId];
  if (!previous || previous.session_id !== sessionId) return;
  if (state.runtimeInstanceId && runtimeId && state.runtimeInstanceId !== runtimeId) {
    if (event.type === EventType.RUN_STARTED) void state.calibrateRuns();
    return;
  }
  if (runtimeId && !state.runtimeInstanceId) state.observeRuntimeIdentity(runtimeId);
  const terminal = event.type === EventType.RUN_FINISHED || event.type === EventType.RUN_ERROR;
  const lifecycle = {
    ...previous,
    runtime_instance_id: runtimeId ?? previous.runtime_instance_id,
    input_accepted: typeof inputAccepted === "boolean" ? inputAccepted : terminal ? null : previous.input_accepted,
  };
  if (event.type === EventType.RUN_STARTED) {
    useConversationStore.setState((current) => {
      const run = current.runSnapshots[runId];
      if (!run || run.version !== 0) return current;
      return { runSnapshots: { ...current.runSnapshots, [runId]: lifecycle } };
    });
    cancellations.observe(lifecycle);
  }
  if (terminal) {
    const outcome: RunOutcome = ["completed", "failed", "cancelled", "skipped", "expired", "rejected"]
      .includes(String(result.outcome)) ? result.outcome as RunOutcome
      : event.type === EventType.RUN_ERROR ? "failed" : "completed";
    const completed: RunSnapshot = {
      ...lifecycle, status: "terminal", outcome,
      finished_at: new Date(typeof event.timestamp === "number" ? event.timestamp : Date.now()).toISOString(),
    };
    useConversationStore.setState((current) => {
      const run = current.runSnapshots[runId];
      if (!run || run.status === "terminal") return current;
      const view = current.sessionViews[sessionId];
      const messages = view ? terminalizeRunMessages(view.messages, completed) : undefined;
      return {
        runSnapshots: { ...current.runSnapshots, [runId]: completed },
        ...(view && messages ? {
          sessionViews: { ...current.sessionViews, [sessionId]: { ...view, messages } },
          ...(current.currentSessionId === sessionId ? { messages } : {}),
        } : {}),
      };
    });
    cancellations.forget(runId);
    observeInputAcceptance(completed);
  } else observeInputAcceptance(lifecycle);
}

/** Reset module-owned async coordinators as well as Zustand state between isolated tests. */
export function resetConversationCoordinatorsForTests(): void {
  detailReads.reset(); appliedDetails = new WeakSet(); cancellations.reset(); pendingInputs.clear();
  activeTransports.clear(); runtimeGeneration += 1;
}

function hasErrorMessage(messages: ChatMessage[], error: string): boolean {
  return messages.some((message) => message.status === "error" && message.error === error);
}

function rememberLastChatSession(sessionId: string | null): void {
  void setLastChatSessionId(sessionId).catch(() => {
    // UI 恢复状态写入失败不影响当前对话；后续有统一 telemetry 再上报。
  });
}

function applyStreamEvents(message: ChatMessage, events: Parameters<typeof applyAguiEvent>[1][]) {
  return events.reduce((next, event) => applyAguiEvent(next, event), message);
}

function sessionViewKey(sessionId: string | null): string {
  return sessionId ?? HOME_VIEW_KEY;
}

function currentViewSnapshot(state: ConversationState): SessionConversationView {
  return {
    messages: state.messages,
    currentModel: state.currentModel,
    currentReasoningPreset: state.currentReasoningPreset,
    approvalMode: state.approvalMode,
    approvalModeVersion: state.approvalModeVersion,
    historyLoading: state.historyLoading,
    historyLoadSeq: state.historyLoadSeq,
    syncRefreshSeq: state.syncRefreshSeq,
    revision: state.sessionViews[sessionViewKey(state.currentSessionId)]?.revision ?? 0,
  };
}

function emptyView(): SessionConversationView {
  return {
    messages: [],
    currentModel: null,
    currentReasoningPreset: null,
    approvalMode: "per_request",
    approvalModeVersion: 0,
    historyLoading: false,
    historyLoadSeq: 0,
    syncRefreshSeq: 0,
    revision: 0,
  };
}

function newConversationDraftView(): SessionConversationView {
  const runtimeConfig = resolveDefaultConversationRuntimeConfig();
  return {
    ...emptyView(),
    currentModel: runtimeConfig.model,
    // 首帧时 catalog 可能尚未加载；null 表示草稿仍在等待权威默认 preset。
    currentReasoningPreset: modelOption(runtimeConfig.model)
      ? runtimeConfig.reasoningPreset
      : null,
  };
}

function viewFields(view: SessionConversationView) {
  return {
    messages: view.messages,
    currentModel: view.currentModel,
    currentReasoningPreset: view.currentReasoningPreset,
    approvalMode: view.approvalMode,
    approvalModeVersion: view.approvalModeVersion,
    historyLoading: view.historyLoading,
    historyLoadSeq: view.historyLoadSeq,
    syncRefreshSeq: view.syncRefreshSeq,
  };
}

const initialHomeView = newConversationDraftView();

/** Approval versions are independent from session content revisions. */
function approvalModeFields(
  state: ConversationState,
  sessionId: string,
  incoming: Partial<SessionApprovalModeState>,
): Pick<SessionConversationView, "approvalMode" | "approvalModeVersion"> {
  let known = state.currentSessionId === sessionId
    ? { approvalMode: state.approvalMode, approvalModeVersion: state.approvalModeVersion }
    : undefined;
  const cached = state.sessionViews[sessionId];
  if (cached && (!known || cached.approvalModeVersion > known.approvalModeVersion)) {
    known = { approvalMode: cached.approvalMode, approvalModeVersion: cached.approvalModeVersion };
  }
  if (
    incoming.approval_mode !== undefined
    && incoming.approval_mode_version !== undefined
    && (!known || incoming.approval_mode_version > known.approvalModeVersion)
  ) {
    return {
      approvalMode: incoming.approval_mode,
      approvalModeVersion: incoming.approval_mode_version,
    };
  }
  return known ?? { approvalMode: "per_request", approvalModeVersion: 0 };
}

const approvalModeRefresher = createApprovalModeRefresher({
  current: () => ({ sessionId: useConversationStore.getState().currentSessionId, openToken: openSessionToken }),
  load: (sessionId) => sessionsApi.getApprovalMode(sessionId),
  apply: (state) => useConversationStore.getState().applyApprovalModeState(state),
});

export const useConversationStore = create<ConversationState>((set, get) => ({
  currentSessionId: null,
  currentModel: initialHomeView.currentModel,
  currentReasoningPreset: initialHomeView.currentReasoningPreset,
  approvalMode: "per_request",
  approvalModeVersion: 0,
  approvalModeChanging: false,
  modelSwitching: false,
  modelSwitchTarget: null,
  editingMessage: null,
  messages: [],
  historyLoading: false,
  historyLoadSeq: 0,
  syncRefreshSeq: 0,
  sessionViews: { [HOME_VIEW_KEY]: initialHomeView },
  runSnapshots: {},
  runtimeInstanceId: null,
  runCalibrationPhase: "calibrating",
  runFence: emptySnapshotFence(),
  viewAccessedAt: {},
  stopRequestsByRun: {},
  unreadRunOutcomeBySession: {},
  detachedSessionId: null,
  searchTarget: null,

  newSession() {
    if (get().editingMessage?.phase === "submitting") return;
    get().cancelEditing();
    openSessionToken += 1;
    detailReads.invalidateLifecycle();
    detailRefreshSeq += 1;
    modelMutationSeq += 1;
    approvalModeMutationSeq += 1;
    searchNavigationToken += 1;
    rememberLastChatSession(null);
    set((state) => {
      const previousKey = sessionViewKey(state.currentSessionId);
      const home = state.currentSessionId === null
        ? currentViewSnapshot(state)
        : state.sessionViews[HOME_VIEW_KEY] ?? newConversationDraftView();
      return {
        ...viewFields(home),
        currentSessionId: null,
        modelSwitching: false,
        modelSwitchTarget: null,
        approvalModeChanging: false,
        editingMessage: null,
        detachedSessionId: null,
        searchTarget: null,
        sessionViews: {
          ...state.sessionViews,
          [previousKey]: currentViewSnapshot(state),
          [HOME_VIEW_KEY]: home,
        },
        viewAccessedAt: { ...state.viewAccessedAt, [HOME_VIEW_KEY]: Date.now() },
      };
    });
    get().resolveNewConversationDraftRuntimeConfig();
    get().pruneCaches();
  },

  resolveNewConversationDraftRuntimeConfig() {
    set((state) => {
      if (
        state.currentSessionId !== null
        || state.currentModel === null
        || state.currentReasoningPreset !== null
        || modelOption(state.currentModel) === null
      ) {
        return state;
      }
      const runtimeConfig = defaultModelRuntimeConfig(state.currentModel);
      const home = {
        ...currentViewSnapshot(state),
        currentReasoningPreset: runtimeConfig.reasoningPreset,
      };
      return {
        currentReasoningPreset: runtimeConfig.reasoningPreset,
        sessionViews: { ...state.sessionViews, [HOME_VIEW_KEY]: home },
      };
    });
  },

  async openSession(sessionId, options) {
    if (get().editingMessage?.phase === "submitting") return false;
    get().cancelEditing();
    const failureMode = options?.failureMode ?? "message";
    const remember = options?.remember ?? true;
    const detached = options?.detached ?? false;
    const previousSessionId = get().currentSessionId;
    const previousDetachedSessionId = get().detachedSessionId;
    const token = openSessionToken + 1;
    openSessionToken = token;
    detailReads.invalidateLifecycle();
    detailRefreshSeq += 1;
    modelMutationSeq += 1;
    approvalModeMutationSeq += 1;
    if (remember) rememberLastChatSession(sessionId);
    const cached = get().sessionViews[sessionId];
    const epoch = runtimeGeneration;
    const activeRun = sessionActiveRun(get(), sessionId);
    set((state) => ({
      ...viewFields(
        cached
          ? cached
          : { ...emptyView(), historyLoading: true },
      ),
      currentSessionId: sessionId,
      historyLoading: cached && activeRun ? false : true,
      modelSwitching: false,
      modelSwitchTarget: null,
      approvalModeChanging: false,
      editingMessage: null,
      detachedSessionId: detached ? sessionId : null,
      searchTarget: options?.searchTarget ?? null,
      sessionViews: {
        ...state.sessionViews,
        [sessionViewKey(state.currentSessionId)]: currentViewSnapshot(state),
      },
      viewAccessedAt: { ...state.viewAccessedAt, [sessionId]: Date.now() },
    }));
    // Only explicitly opening an existing session triggers this read. Sending a
    // first message selects an optimistic ID before the backend creates it.
    void get().refreshApprovalMode(sessionId);
    if (cached && activeRun) {
      get().markSessionViewed(sessionId);
      get().pruneCaches();
      return true;
    }
    try {
      const detail = await detailReads.read(sessionId);
      if (token !== openSessionToken || get().currentSessionId !== sessionId || epoch !== runtimeGeneration) return false;
      if ((get().sessionViews[sessionId]?.revision ?? 0) > (detail.revision ?? 0)) {
        set((state) => {
          if (token !== openSessionToken || state.currentSessionId !== sessionId
            || epoch !== runtimeGeneration) return state;
          const newer = state.sessionViews[sessionId];
          if (!newer) return state;
          const view = { ...newer, historyLoading: false, historyLoadSeq: state.historyLoadSeq + 1 };
          return { ...viewFields(view), sessionViews: { ...state.sessionViews, [sessionId]: view } };
        });
        if (token !== openSessionToken || get().currentSessionId !== sessionId
          || epoch !== runtimeGeneration) return false;
        get().markSessionViewed(sessionId);
        get().pruneCaches();
        return true;
      }
      appliedDetails.add(detail);
      set((s) => {
        const view = {
          messages: projectSessionDetail(detail),
          currentModel: detail.model,
          currentReasoningPreset: detail.reasoningPreset
            ?? defaultModelRuntimeConfig(detail.model).reasoningPreset,
          ...approvalModeFields(s, sessionId, detail),
          historyLoading: false,
          historyLoadSeq: s.historyLoadSeq + 1,
          syncRefreshSeq: s.syncRefreshSeq,
          revision: detail.revision ?? 0,
        };
        return {
          ...viewFields(view),
          sessionViews: { ...s.sessionViews, [sessionId]: view },
        };
      });
      get().markSessionViewed(sessionId);
      get().pruneCaches();
      return true;
    } catch {
      if (token !== openSessionToken || get().currentSessionId !== sessionId || epoch !== runtimeGeneration) return false;
      if (failureMode === "home") {
        set((s) => {
          const home = s.sessionViews[HOME_VIEW_KEY] ?? newConversationDraftView();
          return {
            ...viewFields(home),
            currentSessionId: null,
            modelSwitching: false,
            modelSwitchTarget: null,
            approvalModeChanging: false,
            editingMessage: null,
            detachedSessionId: null,
            searchTarget: null,
            historyLoadSeq: s.historyLoadSeq + 1,
          };
        });
        get().resolveNewConversationDraftRuntimeConfig();
        return false;
      }
      if (failureMode === "previous") {
        if (remember) rememberLastChatSession(previousSessionId);
        set((s) => {
          const previous = s.sessionViews[sessionViewKey(previousSessionId)] ?? emptyView();
          return {
            ...viewFields(previous),
            currentSessionId: previousSessionId,
            detachedSessionId: previousDetachedSessionId,
            searchTarget: null,
            modelSwitching: false,
            modelSwitchTarget: null,
            approvalModeChanging: false,
            editingMessage: null,
            historyLoadSeq: s.historyLoadSeq + 1,
          };
        });
        return false;
      }
      set((s) => ({
        historyLoading: false,
        historyLoadSeq: s.historyLoadSeq + 1,
        messages: [
          {
            id: newId(),
            role: "assistant",
            createdAt: new Date().toISOString(),
            blocks: [],
            status: "error",
            error: "这段对话我一时没翻出来，待会儿再试试？",
          },
        ],
      }));
      return false;
    }
  },

  async openSearchResult(result, query) {
    searchNavigationToken += 1;
    const targetToken = searchNavigationToken;
    const isCurrentSession = get().currentSessionId === result.session_id;

    if (result.kind === "title") {
      if (isCurrentSession) {
        set({
          detachedSessionId: result.archived ? result.session_id : null,
          searchTarget: null,
        });
        if (!result.archived) rememberLastChatSession(result.session_id);
        return true;
      }
      return get().openSession(result.session_id, {
        detached: result.archived,
        failureMode: "previous",
        remember: !result.archived,
      });
    }

    const navigationTarget: SearchNavigationTarget = {
      token: targetToken,
      sessionId: result.session_id,
      anchorEventUuid: result.anchor_event_uuid,
      query,
    };

    if (!isCurrentSession) {
      const opened = await get().openSession(result.session_id, {
        detached: result.archived,
        failureMode: "previous",
        remember: !result.archived,
        searchTarget: navigationTarget,
      });
      if (!opened || targetToken !== searchNavigationToken) return false;
    } else {
      set({
        detachedSessionId: result.archived ? result.session_id : null,
        searchTarget: navigationTarget,
      });
    }

    if (get().currentSessionId !== result.session_id || targetToken !== searchNavigationToken) {
      return false;
    }
    return true;
  },

  clearSearchTarget(token) {
    set((state) => (state.searchTarget?.token === token ? { searchTarget: null } : state));
  },

  async refreshCurrentSession(sessionId) {
    const seq = detailRefreshSeq + 1;
    detailRefreshSeq = seq;
    const openToken = openSessionToken;
    const epoch = runtimeGeneration;
    if (get().currentSessionId !== sessionId || isSessionBusy(get(), sessionId)) return "stale";
    try {
      const detail = await detailReads.read(sessionId);
      if (
        seq !== detailRefreshSeq ||
        openToken !== openSessionToken ||
        epoch !== runtimeGeneration ||
        get().currentSessionId !== sessionId ||
        isSessionBusy(get(), sessionId)
      ) {
        return "stale";
      }
      const revision = detail.revision ?? 0;
      if ((get().sessionViews[sessionId]?.revision ?? 0) > revision) return "stale";
      if (appliedDetails.has(detail)) return "stale";
      appliedDetails.add(detail);
      set((state) => {
        const view: SessionConversationView = {
          messages: projectSessionDetail(detail),
          currentModel: detail.model,
          currentReasoningPreset: detail.reasoningPreset
            ?? defaultModelRuntimeConfig(detail.model).reasoningPreset,
          ...approvalModeFields(state, sessionId, detail),
          historyLoading: false,
          historyLoadSeq: state.historyLoadSeq,
          syncRefreshSeq: state.syncRefreshSeq + 1,
          revision,
        };
        return {
          ...viewFields(view),
          sessionViews: { ...state.sessionViews, [sessionId]: view },
        };
      });
      return "applied";
    } catch {
      return seq === detailRefreshSeq && epoch === runtimeGeneration ? "failed" : "stale";
    }
  },

  invalidateBackgroundRefreshes() {
    detailRefreshSeq += 1;
    detailReads.invalidateLifecycle();
    approvalModeRefresher.invalidate();
  },

  invalidateSessionDetail(sessionId) {
    detailReads.invalidate(sessionId);
  },

  observeRuntimeIdentity(runtimeId) {
    const previous = get().runtimeInstanceId;
    if (previous === runtimeId) return;
    if (previous !== null) {
      runtimeGeneration += 1;
      detailReads.invalidateLifecycle();
      appliedDetails = new WeakSet();
    }
    cancellations.setRuntime(runtimeId);
    set((state) => ({
      runtimeInstanceId: runtimeId,
      runSnapshots: Object.fromEntries(Object.entries(state.runSnapshots).filter(([, run]) =>
        !run.runtime_instance_id || run.runtime_instance_id === runtimeId)),
    }));
  },

  applyApprovalModeState(state) {
    set((current) => {
      const sessionId = state.session_id;
      const existing = current.sessionViews[sessionId];
      const fields = approvalModeFields(current, sessionId, state);
      const nextView = existing
        ? {
            ...existing,
            ...fields,
          }
        : existing;
      return {
        sessionViews: nextView
          ? { ...current.sessionViews, [sessionId]: nextView }
          : current.sessionViews,
        ...(current.currentSessionId === sessionId
          ? {
              ...fields,
            }
          : {}),
      };
    });
  },

  refreshApprovalMode(sessionId, options) {
    return approvalModeRefresher.refresh(sessionId, options);
  },

  applyRunEvents(sessionId, runId, events) {
    if (events.length === 0) return;
    detailReads.invalidate(sessionId);
    set((state) => {
      const knownRun = state.runSnapshots[runId];
      if (knownRun && knownRun.session_id !== sessionId) return state;
      if (knownRun?.status === "terminal") return state;
      const activeRun = sessionActiveRun(state, sessionId);
      if (!knownRun && activeRun && activeRun.run_id !== runId) return state;

      const isCurrent = state.currentSessionId === sessionId;
      const existingView = state.sessionViews[sessionId];
      const view = existingView ?? (isCurrent ? currentViewSnapshot(state) : undefined);
      if (!view) return state;

      const existingIndex = findRunAssistantIndex(view.messages, runId);
      const assistantId = existingIndex >= 0
        ? view.messages[existingIndex]!.id
        : `proactive:${runId}`;
      const assistant: ChatMessage = existingIndex >= 0
        ? view.messages[existingIndex]
        : {
            id: assistantId,
            role: "assistant",
            runId,
            createdAt: new Date().toISOString(),
            blocks: [],
            status: "streaming",
          };
      const nextAssistant = events.reduce(
        (message, event) => applyPushConversationEvent(
          message,
          withObservedPushTerminalTimestamp(event),
          runId,
        ),
        assistant,
      );
      const messages = [...view.messages];
      if (existingIndex >= 0) messages[existingIndex] = nextAssistant;
      else messages.push(nextAssistant);
      const nextView: SessionConversationView = { ...view, messages };

      return {
        sessionViews: { ...state.sessionViews, [sessionId]: nextView },
        ...(isCurrent ? { messages } : {}),
      };
    });
  },

  applyRunSnapshot(snapshot) {
    if (snapshot.runtime_instance_id && !get().runtimeInstanceId) {
      get().observeRuntimeIdentity(snapshot.runtime_instance_id);
    }
    let accepted = false;
    set((state) => {
      const previous = state.runSnapshots[snapshot.run_id];
      if (previous && previous.session_id !== snapshot.session_id) return state;
      if (state.runtimeInstanceId && snapshot.runtime_instance_id
        && state.runtimeInstanceId !== snapshot.runtime_instance_id) return state;
      if (previous?.status === "terminal" && snapshot.status !== "terminal") return state;
      if (previous && previous.version >= snapshot.version) return state;
      accepted = true;
      const terminal = snapshot.status === "terminal";
      const stopRequestsByRun = { ...state.stopRequestsByRun };
      if (terminal) delete stopRequestsByRun[snapshot.run_id];
      const view = state.sessionViews[snapshot.session_id];
      const nextView = view
        ? {
            ...view,
            messages: terminal ? terminalizeRunMessages(view.messages, snapshot) : view.messages,
          }
        : view;
      const isCurrent = state.currentSessionId === snapshot.session_id;
      const unreadRunOutcomeBySession = { ...state.unreadRunOutcomeBySession };
      if (terminal && !isCurrent && snapshot.outcome === "failed") {
        unreadRunOutcomeBySession[snapshot.session_id] = "failed";
      }
      const runSnapshots = { ...state.runSnapshots, [snapshot.run_id]: snapshot };
      const terminalRuns = Object.values(runSnapshots)
        .filter((run) => run.status === "terminal")
        .sort((left, right) =>
          (right.finished_at ?? right.started_at ?? right.created_at).localeCompare(
            left.finished_at ?? left.started_at ?? left.created_at,
          ),
        );
      for (const expired of terminalRuns.slice(MAX_RUN_SNAPSHOTS)) {
        delete runSnapshots[expired.run_id];
      }
      const runFence = pruneSnapshotFence(
        touchSnapshotKey(state.runFence, snapshot.run_id),
        new Set(Object.keys(runSnapshots)),
      );
      return {
        runSnapshots,
        runFence,
        stopRequestsByRun,
        unreadRunOutcomeBySession,
        sessionViews: nextView
          ? { ...state.sessionViews, [snapshot.session_id]: nextView }
          : state.sessionViews,
        ...(isCurrent && nextView ? { messages: nextView.messages } : {}),
      };
    });
    if (!accepted) return;
    observeInputAcceptance(snapshot);
    cancellations.observe(snapshot);
    if (snapshot.status === "terminal") {
      detailReads.invalidate(snapshot.session_id);
      activeTransports.delete(snapshot.run_id);
      void markCompletedRunUnread(snapshot);
    }
    get().pruneCaches();
  },

  async calibrateRuns() {
    cancellations.restore();
    for (const intent of cancellations.identities()) {
      if (get().runSnapshots[intent.runId]) continue;
      set((state) => ({ runSnapshots: {
        ...state.runSnapshots,
        [intent.runId]: {
          ...optimisticRunSnapshot(intent.runId, intent.sessionId, new Date().toISOString()),
          runtime_instance_id: intent.runtimeId,
        },
      } }));
    }
    const calibrationSeq = runCalibrationSeq + 1;
    runCalibrationSeq = calibrationSeq;
    const requestStartEpoch = get().runFence.mutationEpoch;
    const requestRuntimeGeneration = runtimeGeneration;
    set({ runCalibrationPhase: "calibrating" });
    try {
      const response = await runsApi.list(true);
      if (calibrationSeq !== runCalibrationSeq || requestRuntimeGeneration !== runtimeGeneration) return;
      const incomingRuntimeId = response.runs.find((run) => run.runtime_instance_id)?.runtime_instance_id;
      if (incomingRuntimeId) get().observeRuntimeIdentity(incomingRuntimeId);
      for (const snapshot of response.runs) get().applyRunSnapshot(snapshot);
      const returnedRunIds = new Set(response.runs.map((snapshot) => snapshot.run_id));
      const sessionsToRefresh = new Set<string>();
      set((state) => {
        const runSnapshots = { ...state.runSnapshots };
        const stopRequestsByRun = { ...state.stopRequestsByRun };
        for (const [runId, run] of Object.entries(runSnapshots)) {
          if (
            run.status === "terminal"
            || cancellations.has(runId)
            || (run.version === 0 && (activeTransports.has(runId) || pendingInputs.has(runId)))
            || returnedRunIds.has(runId)
            || !canReconcileSnapshotAbsence(state.runFence, runId, requestStartEpoch)
          ) continue;
          delete runSnapshots[runId];
          delete stopRequestsByRun[runId];
          sessionsToRefresh.add(run.session_id);
        }
        return {
          runSnapshots,
          stopRequestsByRun,
          runCalibrationPhase: "ready",
          runFence: pruneSnapshotFence(state.runFence, new Set(Object.keys(runSnapshots))),
        };
      });
      void cancellations.retry();
      const currentSessionId = get().currentSessionId;
      if (currentSessionId && sessionsToRefresh.has(currentSessionId)) {
        void get().refreshCurrentSession(currentSessionId);
      }
    } catch {
      if (calibrationSeq === runCalibrationSeq) set({ runCalibrationPhase: "degraded" });
    }
  },

  markSessionViewed(sessionId) {
    set((state) => {
      if (!(sessionId in state.unreadRunOutcomeBySession)) return state;
      const unreadRunOutcomeBySession = { ...state.unreadRunOutcomeBySession };
      delete unreadRunOutcomeBySession[sessionId];
      return { unreadRunOutcomeBySession };
    });
  },

  pruneCaches() {
    set((state) => {
      const draftWorkspaces = useDraftStore.getState().workspaces;
      const protectedKeys = new Set<string>([
        sessionViewKey(state.currentSessionId),
        ...Object.values(state.runSnapshots)
          .filter((run) => run.status !== "terminal")
          .map((run) => run.session_id),
      ]);
      for (const [key, draft] of Object.entries(draftWorkspaces)) {
        if (draft.text || draft.attachments.length > 0 || draft.references.length > 0) {
          protectedKeys.add(key === HOME_DRAFT_KEY ? HOME_VIEW_KEY : key);
        }
      }
      const idleKeys = Object.keys(state.sessionViews)
        .filter((key) => !protectedKeys.has(key))
        .sort(
          (left, right) =>
            (state.viewAccessedAt[right] ?? 0) - (state.viewAccessedAt[left] ?? 0),
        );
      if (idleKeys.length <= MAX_IDLE_SESSION_VIEWS) return state;
      const evicted = new Set(idleKeys.slice(MAX_IDLE_SESSION_VIEWS));
      const sessionViews = Object.fromEntries(
        Object.entries(state.sessionViews).filter(([key]) => !evicted.has(key)),
      );
      const viewAccessedAt = Object.fromEntries(
        Object.entries(state.viewAccessedAt).filter(([key]) => !evicted.has(key)),
      );
      return { sessionViews, viewAccessedAt };
    });
  },

  async selectModel(modelId) {
    return get().selectRuntimeConfig(defaultModelRuntimeConfig(modelId));
  },

  async selectRuntimeConfig(config) {
    const current = get();
    const effectiveModel = current.currentModel;
    const effectiveReasoningPreset = effectiveModel
      ? current.currentReasoningPreset
        ?? defaultModelRuntimeConfig(effectiveModel).reasoningPreset
      : null;
    if (
      effectiveModel !== null
      && config.model === effectiveModel
      && config.reasoningPreset === effectiveReasoningPreset
    ) {
      return {
        status: "applied",
        model: effectiveModel,
        reasoningPreset: effectiveReasoningPreset,
      };
    }
    if (
      isSessionBusy(current, current.currentSessionId)
      || current.historyLoading
      || current.editingMessage?.phase === "submitting"
    ) {
      return {
        status: "failed",
        error: { friendly: "请先结束当前操作，再切换模型。", code: "session_busy" },
      };
    }

    const seq = modelMutationSeq + 1;
    modelMutationSeq = seq;
    detailRefreshSeq += 1;
    const sessionId = current.currentSessionId;
    const openToken = openSessionToken;
    set({ modelSwitching: true, modelSwitchTarget: config.model });
    try {
      const appliedConfig = sessionId
        ? (await sessionsApi.updateRuntimeConfig(sessionId, config)).runtimeConfig
        : (await modelsApi.validate(config)).runtimeConfig ?? config;
      if (
        seq !== modelMutationSeq ||
        openToken !== openSessionToken ||
        get().currentSessionId !== sessionId
      ) {
        return { status: "stale" };
      }
      set({
        currentModel: appliedConfig.model,
        currentReasoningPreset: appliedConfig.reasoningPreset,
      });
      return { status: "applied", ...appliedConfig };
    } catch (error) {
      if (
        seq !== modelMutationSeq ||
        openToken !== openSessionToken ||
        get().currentSessionId !== sessionId
      ) {
        return { status: "stale" };
      }
      return { status: "failed", error: asFriendlyError(error) };
    } finally {
      if (seq === modelMutationSeq) {
        set({ modelSwitching: false, modelSwitchTarget: null });
      }
    }
  },

  async setApprovalMode(mode, riskAcknowledged) {
    const current = get();
    const sessionId = current.currentSessionId;
    if (!sessionId) {
      if (mode === "session_auto" && !riskAcknowledged) {
        return {
          status: "failed",
          error: {
            friendly: "进入本会话无需授权前必须重新确认风险。",
            code: "approval_risk_acknowledgement_required",
          },
        };
      }
      if (current.approvalMode === mode) {
        return { status: "applied", mode, version: current.approvalModeVersion };
      }
      set({ approvalMode: mode, approvalModeVersion: 0 });
      return { status: "applied", mode, version: 0 };
    }
    if (current.approvalMode === mode) {
      return { status: "applied", mode, version: current.approvalModeVersion };
    }
    const seq = approvalModeMutationSeq + 1;
    approvalModeMutationSeq = seq;
    const openToken = openSessionToken;
    set({ approvalModeChanging: true });
    try {
      const state = await sessionsApi.updateApprovalMode(
        sessionId,
        mode,
        current.approvalModeVersion,
        riskAcknowledged,
      );
      if (
        seq !== approvalModeMutationSeq
        || openToken !== openSessionToken
        || get().currentSessionId !== sessionId
      ) return { status: "stale" };
      get().applyApprovalModeState(state);
      return {
        status: "applied",
        mode: state.approval_mode,
        version: state.approval_mode_version,
      };
    } catch (error) {
      if (
        seq !== approvalModeMutationSeq
        || openToken !== openSessionToken
        || get().currentSessionId !== sessionId
      ) return { status: "stale" };
      await get().refreshApprovalMode(sessionId, { invalidate: true });
      return { status: "failed", error: asApprovalModeFriendlyError(error) };
    } finally {
      if (seq === approvalModeMutationSeq) set({ approvalModeChanging: false });
    }
  },

  beginEditingMessage(messageId) {
    const current = get();
    if (
      isSessionBusy(current, current.currentSessionId)
      || current.historyLoading
      || !current.currentSessionId
      || current.editingMessage?.phase === "submitting"
    ) return false;
    const target = [...current.messages].reverse().find((message) => message.role === "user");
    if (!target || target.id !== messageId) return false;
    if (current.editingMessage?.messageId === messageId) return true;

    const key = editDraftKey(current.currentSessionId, messageId);
    const initialAttachments: DraftAttachmentState[] = (target.attachments ?? []).map(
      (attachment, index) => ({
        status: "ready",
        source: "existing",
        localId: `existing:${messageId}:${index}`,
        attachment,
      }),
    );
    useDraftStore.getState().initialize(
      key,
      messageText(target),
      initialAttachments,
      [...(target.references ?? [])],
    );
    set({
      editingMessage: {
        sessionId: current.currentSessionId,
        messageId,
        draftKey: key,
        originalText: messageText(target),
        originalAttachments: [...(target.attachments ?? [])],
        originalReferences: [...(target.references ?? [])],
        phase: "editing",
      },
    });
    return true;
  },

  cancelEditing() {
    const editing = get().editingMessage;
    if (!editing) return;
    pendingEditAdmissionBySession.get(editing.sessionId)?.abort();
    pendingEditAdmissionBySession.delete(editing.sessionId);
    useDraftStore.getState().discard(editing.draftKey);
    set((state) => state.editingMessage?.messageId === editing.messageId
      ? { editingMessage: null }
      : state);
  },

  send(text, attachments = [], references = []) {
    const trimmed = text.trim();
    if (!trimmed && attachments.length === 0 && references.length === 0) {
      return { accepted: false, reason: "empty" };
    }
    if (isSessionBusy(get(), get().currentSessionId)) {
      return { accepted: false, reason: "busy" };
    }
    if (get().historyLoading) return { accepted: false, reason: "history-loading" };
    if (get().modelSwitching) return { accepted: false, reason: "model-switching" };
    if (get().editingMessage) return { accepted: false, reason: "editing" };

    let runtimeState = get();
    if (runtimeState.currentSessionId === null && runtimeState.currentModel === null) {
      const normalizedDraft = newConversationDraftView();
      set((state) => {
        if (state.currentSessionId !== null || state.currentModel !== null) return state;
        const home = {
          ...currentViewSnapshot(state),
          currentModel: normalizedDraft.currentModel,
          currentReasoningPreset: normalizedDraft.currentReasoningPreset,
        };
        return {
          currentModel: home.currentModel,
          currentReasoningPreset: home.currentReasoningPreset,
          sessionViews: { ...state.sessionViews, [HOME_VIEW_KEY]: home },
        };
      });
      runtimeState = get();
    }
    if (
      runtimeState.currentModel === null
      || runtimeState.currentReasoningPreset === null
    ) {
      return { accepted: false, reason: "model-unavailable" };
    }

    const existingSessionId = runtimeState.currentSessionId;
    const sessionId = existingSessionId ?? newId();
    const runId = newId();
    const initialApprovalMode = runtimeState.approvalMode;
    const shouldBootstrapApprovalMode = initialApprovalMode === "session_auto"
      && runtimeState.messages.length === 0;
    const initialApprovalModeVersion = shouldBootstrapApprovalMode
      ? Math.max(1, runtimeState.approvalModeVersion)
      : runtimeState.approvalModeVersion;
    const initialConfig = {
      model: runtimeState.currentModel,
      reasoningPreset: runtimeState.currentReasoningPreset,
    };
    const initialModel = initialConfig.model;
    const credentialGroup = runtimeCredentialGroupForModel(initialModel);
    const credentialRevision = credentialGroup
      ? useRuntimeCredentialStore
          .getState()
          .groups.find((group) => group.group === credentialGroup)?.credentialRevision
      : null;
    const assistantId = newId();
    const sentAt = new Date().toISOString();
    const userMessageId = newId();
    const sendEpoch = runtimeGeneration;
    const chatAttachments: ChatAttachment[] = attachments.map((draft) => ({
      assetId: draft.attachment.assetId,
      displayName: draft.attachment.displayName,
      kind: draft.attachment.kind,
      mediaType: draft.attachment.mediaType,
      sizeBytes: draft.attachment.sizeBytes,
    }));
    const placeholder: ChatMessage = {
      id: assistantId,
      role: "assistant",
      runId,
      createdAt: sentAt,
      blocks: [],
      status: "streaming",
    };
    set((s) => {
      const messages = [
        ...s.messages,
        userMessage(trimmed, sentAt, chatAttachments, userMessageId, references),
        placeholder,
      ];
      const view: SessionConversationView = {
        messages,
        currentModel: initialModel,
        currentReasoningPreset: initialConfig.reasoningPreset,
        approvalMode: initialApprovalMode,
        approvalModeVersion: initialApprovalModeVersion,
        historyLoading: false,
        historyLoadSeq: s.historyLoadSeq,
        syncRefreshSeq: s.syncRefreshSeq,
        revision: s.sessionViews[sessionId]?.revision ?? 0,
      };
      const sessionViews = {
        ...s.sessionViews,
        [sessionId]: view,
      };
      if (s.currentSessionId === null) {
        delete sessionViews[HOME_VIEW_KEY];
      }
      return {
        currentSessionId: sessionId,
        currentModel: initialModel,
        currentReasoningPreset: initialConfig.reasoningPreset,
        approvalMode: initialApprovalMode,
        approvalModeVersion: initialApprovalModeVersion,
        messages,
        sessionViews,
        runSnapshots: {
          ...s.runSnapshots,
          [runId]: {
            ...optimisticRunSnapshot(runId, sessionId, sentAt),
            runtime_instance_id: runtimeState.runtimeInstanceId,
            input_accepted: false,
          },
        },
        runFence: touchSnapshotKey(s.runFence, runId),
        viewAccessedAt: { ...s.viewAccessedAt, [sessionId]: Date.now() },
      };
    });
    rememberLastChatSession(sessionId);
    pendingInputs.set(runId, {
      // Bind the input to the first authoritative lifecycle response, not an optimistic owner.
      sessionId, runtimeId: null, userMessageId, assistantId,
    });
    detailReads.invalidate(sessionId);

    const controller = new AbortController();
    activeTransports.set(runId, { sessionId, controller });
    const projectDirectStream = !isTauri();

    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set((s) => {
        if (sendEpoch !== runtimeGeneration) return s;
        const existing = s.sessionViews[sessionId];
        if (!existing) return s;
        const messages = existing.messages.map((m) => (m.id === assistantId ? fn(m) : m));
        return {
          sessionViews: {
            ...s.sessionViews,
            [sessionId]: { ...existing, messages },
          },
          messages: s.currentSessionId === sessionId ? messages : s.messages,
        };
      });
    const streamUpdates = projectDirectStream
      ? createStreamUpdateScheduler((events) => {
          patch((m) => applyStreamEvents(m, events));
        })
      : null;

    const completion = (async (): Promise<SendOutcome> => {
      let outcome: SendOutcome = "failed";
      let sawStreamEvent = false;
      let sawTerminalEvent = false;
      let admissionFailed = false;
      try {
        for await (const evt of runAgentStream(
          {
            threadId: sessionId,
            runId,
            modelConfig: initialConfig,
            initialApprovalMode: shouldBootstrapApprovalMode
              ? initialApprovalMode
              : undefined,
            text: trimmed,
            messageId: userMessageId,
            attachments,
            references,
          },
          { signal: controller.signal },
        )) {
          sawStreamEvent = true;
          observeSendLifecycle(evt, sessionId, runId);
          if (evt.type === EventType.RUN_FINISHED || evt.type === EventType.RUN_ERROR) {
            sawTerminalEvent = true;
          }
          if (
            evt.type === EventType.RUN_ERROR &&
            evt.code === "auth_failed" &&
            credentialGroup &&
            credentialRevision
          ) {
            void useRuntimeCredentialStore
              .getState()
              .reportAuthFailure(credentialGroup, credentialRevision)
              .catch(() => undefined);
          }
          streamUpdates?.enqueue(withObservedTerminalTimestamp(evt));
        }
        streamUpdates?.flush();
        if (projectDirectStream) {
          patch((m) => (m.status === "streaming" ? markMessageTerminal(m, "complete") : m));
        }
        const assistantStatus = get().sessionViews[sessionId]?.messages
          .find((message) => message.id === assistantId)?.status;
        outcome = assistantStatus === "error"
          ? "failed"
          : assistantStatus === "interrupted"
            ? "interrupted"
            : "completed";
      } catch (error) {
        streamUpdates?.flush();
        admissionFailed = error instanceof StreamAdmissionError;
        if (controller.signal.aborted) {
          outcome = "interrupted";
          patch((m) => (m.status === "streaming" ? markMessageTerminal(m, "interrupted") : m));
        } else {
          patch((m) => markMessageTerminal(m, "error", { error: FRIENDLY_FALLBACK }));
        }
      } finally {
        streamUpdates?.dispose();
        activeTransports.delete(runId);
        let runStillActive = false;
        try {
          const snapshot = await runsApi.get(runId);
          get().applyRunSnapshot(snapshot);
          const latest = get().runSnapshots[runId] ?? snapshot;
          runStillActive = latest.status !== "terminal";
          if (snapshot.status === "terminal" && get().currentSessionId === sessionId) {
            void get().refreshCurrentSession(sessionId);
          }
          if (latest.outcome === "cancelled") outcome = "interrupted";
          else if (latest.outcome === "rejected") {
            outcome = latest.input_accepted === true ? "failed" : "rejected";
          } else if (latest.outcome === "failed") outcome = "failed";
        } catch {
          // push/reconnect calibration remains the fallback when the transport vanished early.
          if (!sawStreamEvent && admissionFailed) outcome = "rejected";
          runStillActive = !sawTerminalEvent && !admissionFailed;
          if (!runStillActive) dropOptimisticRun(runId);
        }
        if (outcome === "rejected") settleSubmittedInput(runId, true);
        else if (!runStillActive) settleSubmittedInput(runId, false);
        set((s) => {
          const existing = s.sessionViews[sessionId];
          if (!existing) return s;
          const next = {
            ...existing,
            messages:
              outcome === "rejected"
                ? existing.messages.filter(
                    (message) =>
                      message.id !== userMessageId && message.id !== assistantId,
                  )
                : existing.messages,
          };
          const stopRequestsByRun = { ...s.stopRequestsByRun };
          if (!runStillActive) delete stopRequestsByRun[runId];
          const runSnapshots = { ...s.runSnapshots };
          let runFence = s.runFence;
          if (
            outcome === "rejected"
            && runSnapshots[runId]?.version === 0
          ) {
            delete runSnapshots[runId];
            runFence = pruneSnapshotFence(runFence, new Set(Object.keys(runSnapshots)));
          }
          return {
            sessionViews: { ...s.sessionViews, [sessionId]: next },
            runSnapshots,
            runFence,
            stopRequestsByRun,
            messages: s.currentSessionId === sessionId ? next.messages : s.messages,
          };
        });
        void useSessionsStore.getState().refresh();
      }
      return outcome;
    })();

    return { accepted: true, sessionId, runId, completion };
  },

  async editResendLatest(text, attachments, optimisticAttachments, references) {
    const trimmed = text.trim();
    const state = get();
    const editing = state.editingMessage;
    const effectiveReferences = references ?? editing?.originalReferences ?? [];
    if (
      (!trimmed && attachments.length === 0 && effectiveReferences.length === 0) ||
      isSessionBusy(state, state.currentSessionId) ||
      state.historyLoading ||
      state.modelSwitching ||
      !state.currentSessionId ||
      !editing ||
      editing.phase !== "editing" ||
      editing.sessionId !== state.currentSessionId
    ) return "rejected";

    const latestUserIndex = [...state.messages]
      .reverse()
      .findIndex((m) => m.role === "user");
    if (latestUserIndex === -1) return "rejected";
    const targetIndex = state.messages.length - 1 - latestUserIndex;
    const target = state.messages[targetIndex];
    if (!target || target.id !== editing.messageId || target.role !== "user") {
      return "rejected";
    }

    const sessionId = state.currentSessionId;
    const runId = newId();
    const assistantId = newId();
    const sentAt = new Date().toISOString();
    const placeholder: ChatMessage = {
      id: assistantId,
      role: "assistant",
      runId,
      createdAt: sentAt,
      blocks: [],
      status: "streaming",
    };

    const admittedMessages = [
      ...state.messages.slice(0, targetIndex),
      userMessage(trimmed, sentAt, optimisticAttachments, newId(), effectiveReferences),
      placeholder,
    ];
    set((current) => current.editingMessage?.messageId === editing.messageId
      ? { editingMessage: { ...editing, phase: "submitting" } }
      : current);

    const controller = new AbortController();
    activeTransports.set(runId, { sessionId, controller });
    pendingEditAdmissionBySession.set(sessionId, controller);
    const projectDirectStream = !isTauri();

    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set((s) => {
        const view = s.sessionViews[sessionId];
        if (!view) return s;
        const nextMessages = view.messages.map((m) =>
          m.id === assistantId ? fn(m) : m,
        );
        return {
          sessionViews: {
            ...s.sessionViews,
            [sessionId]: { ...view, messages: nextMessages },
          },
          messages: s.currentSessionId === sessionId ? nextMessages : s.messages,
        };
      });
    const streamUpdates = projectDirectStream
      ? createStreamUpdateScheduler((events) => {
          patch((m) => applyStreamEvents(m, events));
        })
      : null;

    let admitted = false;
    let sawTerminalEvent = false;
    let outcome: SendOutcome = "completed";
    let failedError: string | null = null;
    try {
      for await (const evt of editResendLatestStream(
        {
          sessionId,
          runId,
          text: trimmed,
          expectedUserMessageId: editing.messageId,
          expectedUserContent: editing.originalText,
          attachments,
          ...(references !== undefined ? { references: effectiveReferences } : {}),
        },
        {
          signal: controller.signal,
          onAdmitted: () => {
            if (admitted || controller.signal.aborted) return;
            admitted = true;
            pendingEditAdmissionBySession.delete(sessionId);
            useDraftStore.getState().beginSubmission(
              editing.draftKey,
              sessionId,
              runId,
            );
            set((current) => {
              const currentView = current.sessionViews[sessionId] ?? emptyView();
              const earlyRunMessageIndex = findRunAssistantIndex(currentView.messages, runId);
              const earlyRunMessage = earlyRunMessageIndex >= 0
                ? currentView.messages[earlyRunMessageIndex]
                : null;
              const nextMessages = earlyRunMessage
                ? admittedMessages.map((message) => message.id === assistantId
                    ? { ...earlyRunMessage, id: assistantId, runId, createdAt: sentAt }
                    : message)
                : admittedMessages;
              const view = { ...currentView, messages: nextMessages };
              return {
                ...(current.currentSessionId === sessionId
                  ? { messages: nextMessages }
                  : {}),
                editingMessage:
                  current.editingMessage?.messageId === editing.messageId
                    ? null
                    : current.editingMessage,
                sessionViews: {
                  ...current.sessionViews,
                  [sessionId]: view,
                },
                runSnapshots: {
                  ...current.runSnapshots,
                  [runId]: current.runSnapshots[runId]
                    ?? optimisticRunSnapshot(runId, sessionId, sentAt),
                },
                runFence: touchSnapshotKey(current.runFence, runId),
              };
            });
          },
        },
      )) {
        if (evt.type === EventType.RUN_FINISHED || evt.type === EventType.RUN_ERROR) {
          sawTerminalEvent = true;
        }
        streamUpdates?.enqueue(withObservedTerminalTimestamp(evt));
      }
      streamUpdates?.flush();
      const assistant = get().sessionViews[sessionId]?.messages.find(
        (m) => m.id === assistantId,
      );
      if (assistant?.status === "error") {
        failedError = assistant.error ?? FRIENDLY_FALLBACK;
        outcome = "failed";
      } else if (assistant?.status === "interrupted") {
        outcome = "interrupted";
      } else if (projectDirectStream) {
        patch((m) => (m.status === "streaming" ? markMessageTerminal(m, "complete") : m));
      }
    } catch {
      streamUpdates?.flush();
      if (!admitted) {
        outcome = "rejected";
        set((current) => current.editingMessage?.messageId === editing.messageId
          ? {
              editingMessage: {
                ...current.editingMessage,
                phase: "editing",
              },
            }
          : current);
      } else if (controller.signal.aborted) {
        outcome = "interrupted";
        patch((m) => (m.status === "streaming" ? markMessageTerminal(m, "interrupted") : m));
      } else {
        outcome = "failed";
        failedError = FRIENDLY_FALLBACK;
        patch((m) => markMessageTerminal(m, "error", { error: FRIENDLY_FALLBACK }));
      }
    } finally {
      streamUpdates?.dispose();
      activeTransports.delete(runId);
      pendingEditAdmissionBySession.delete(sessionId);
      if (admitted) {
        useDraftStore.getState().finishSubmission(runId, false);
        let runStillActive = controller.signal.aborted;
        try {
          const snapshot = await runsApi.get(runId);
          get().applyRunSnapshot(snapshot);
          runStillActive = (get().runSnapshots[runId] ?? snapshot).status !== "terminal";
        } catch {
          // run_changed / reconnect calibration remains the fallback.
          if (sawTerminalEvent) {
            dropOptimisticRun(runId);
            runStillActive = false;
          }
        }
        set((s) => {
          const view = s.sessionViews[sessionId];
          if (!view) return s;
          const next = view;
          const stopRequestsByRun = { ...s.stopRequestsByRun };
          if (!runStillActive) delete stopRequestsByRun[runId];
          return {
            sessionViews: { ...s.sessionViews, [sessionId]: next },
            stopRequestsByRun,
          };
        });
        if (!runStillActive) {
          try {
            const epoch = runtimeGeneration;
            const detail = await detailReads.read(sessionId);
            const projected = projectSessionDetail(detail);
            set((s) => {
              const view = s.sessionViews[sessionId] ?? emptyView();
              if (epoch !== runtimeGeneration || isSessionBusy(s, sessionId)
                || view.revision > (detail.revision ?? view.revision)) return s;
              appliedDetails.add(detail);
              const nextMessages = failedError
                ? hasErrorMessage(projected, failedError)
                  ? projected
                  : [...projected, assistantErrorMessage(failedError)]
                : projected;
              const nextView = {
                ...view,
                messages: nextMessages,
                currentModel: detail.model,
                currentReasoningPreset: detail.reasoningPreset
                  ?? defaultModelRuntimeConfig(detail.model).reasoningPreset,
                ...approvalModeFields(s, sessionId, detail),
                revision: detail.revision ?? view.revision,
                historyLoadSeq: view.historyLoadSeq + 1,
              };
              return {
                sessionViews: { ...s.sessionViews, [sessionId]: nextView },
                ...(s.currentSessionId === sessionId
                  ? {
                      ...viewFields(nextView),
                      currentModel: detail.model,
                      currentReasoningPreset: detail.reasoningPreset
                        ?? defaultModelRuntimeConfig(detail.model).reasoningPreset,
                    }
                  : {}),
              };
            });
          } catch {
            // 保留乐观态 + 错误占位，避免二次失败把用户带回空白。
          }
        }
        void useSessionsStore.getState().refresh();
      }
    }
    return outcome;
  },

  async continueToolResult(messageId) {
    const state = get();
    const target = state.messages.at(-1);
    if (
      isSessionBusy(state, state.currentSessionId) ||
      state.historyLoading ||
      state.modelSwitching ||
      !state.currentSessionId ||
      target?.id !== messageId ||
      target.status !== "error" ||
      !["upstream_transient", "rate_limit"].includes(target.errorCode ?? "") ||
      !target.blocks.some((block) => block.kind === "tool" && block.status === "done")
    ) return;

    const sessionId = state.currentSessionId;
    const runId = newId();
    const assistantId = newId();
    const placeholder: ChatMessage = {
      id: assistantId,
      role: "assistant",
      runId,
      createdAt: new Date().toISOString(),
      blocks: [],
      status: "streaming",
    };
    const messages = [...state.messages, placeholder];
    set((current) => ({
      messages,
      sessionViews: {
        ...current.sessionViews,
        [sessionId]: {
          ...(current.sessionViews[sessionId] ?? emptyView()),
          messages,
        },
      },
      runSnapshots: {
        ...current.runSnapshots,
        [runId]: optimisticRunSnapshot(runId, sessionId, placeholder.createdAt),
      },
      runFence: touchSnapshotKey(current.runFence, runId),
    }));

    const controller = new AbortController();
    activeTransports.set(runId, { sessionId, controller });
    const projectDirectStream = !isTauri();
    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set((s) => {
        const view = s.sessionViews[sessionId];
        if (!view) return s;
        const nextMessages = view.messages.map((m) =>
          m.id === assistantId ? fn(m) : m,
        );
        return {
          sessionViews: {
            ...s.sessionViews,
            [sessionId]: { ...view, messages: nextMessages },
          },
          messages: s.currentSessionId === sessionId ? nextMessages : s.messages,
        };
      });
    const streamUpdates = projectDirectStream
      ? createStreamUpdateScheduler((events) => {
          patch((m) => applyStreamEvents(m, events));
        })
      : null;
    let sawTerminalEvent = false;

    try {
      for await (const evt of continueToolResultStream(sessionId, runId, {
        signal: controller.signal,
      })) {
        if (evt.type === EventType.RUN_FINISHED || evt.type === EventType.RUN_ERROR) {
          sawTerminalEvent = true;
        }
        streamUpdates?.enqueue(withObservedTerminalTimestamp(evt));
      }
      streamUpdates?.flush();
      if (projectDirectStream) {
        patch((m) => (m.status === "streaming" ? markMessageTerminal(m, "complete") : m));
      }
    } catch {
      streamUpdates?.flush();
      if (controller.signal.aborted) {
        patch((m) => (m.status === "streaming" ? markMessageTerminal(m, "interrupted") : m));
      } else {
        patch((m) => markMessageTerminal(m, "error", { error: FRIENDLY_FALLBACK }));
      }
    } finally {
      streamUpdates?.dispose();
      activeTransports.delete(runId);
      let runStillActive = controller.signal.aborted;
      try {
        const snapshot = await runsApi.get(runId);
        get().applyRunSnapshot(snapshot);
        runStillActive = (get().runSnapshots[runId] ?? snapshot).status !== "terminal";
        if (snapshot.status === "terminal" && get().currentSessionId === sessionId) {
          void get().refreshCurrentSession(sessionId);
        }
      } catch {
        // run_changed / reconnect calibration remains the fallback.
        if (sawTerminalEvent) {
          dropOptimisticRun(runId);
          runStillActive = false;
        }
      }
      set((s) => {
        const view = s.sessionViews[sessionId];
        if (!view) return s;
        const stopRequestsByRun = { ...s.stopRequestsByRun };
        if (!runStillActive) delete stopRequestsByRun[runId];
        return {
          sessionViews: s.sessionViews,
          stopRequestsByRun,
        };
      });
      void useSessionsStore.getState().refresh();
    }
  },

  async stop() {
    const initial = get();
    const sessionId = initial.currentSessionId;
    if (!sessionId) return "noop";
    const presentation = sessionRunPresentation(initial, sessionId);
    const runId = presentation.run?.run_id;
    if (!runId) return "noop";
    if (initial.stopRequestsByRun[runId]) return "already-pending";
    if (!presentation.canStop) return "noop";

    const outcome = await cancellations.request(presentation.run!);
    if (outcome === "pending" && !cancellations.has(runId)) return "noop";
    const run = get().runSnapshots[runId];
    if (run?.status === "terminal" && get().currentSessionId === sessionId) {
      void get().refreshCurrentSession(sessionId);
    }
    return outcome;
  },
}));
