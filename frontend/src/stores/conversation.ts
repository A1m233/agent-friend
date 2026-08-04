/**
 * 当前会话 store（design §4.3/§3.5）：消息列表 + 流式态，消费 AG-UI 事件流。
 *
 * - `send`：乐观插入 user + 占位 assistant，自写 fetch-SSE 消费、reducer 累积。
 * - `openSession`：拉历史事件投影成消息，续聊用同一 session_id 作 threadId。
 * - 错误统一为 turn 状态，不暴露技术细节。
 */

import { create } from "zustand";
import { EventType } from "@ag-ui/core";
import { setLastChatSessionId } from "@/lib/persistence/chatUi";
import { resolveDefaultConversationModel } from "@/lib/model-config";
import {
  continueToolResultStream,
  editResendLatestStream,
  modelsApi,
  runAgentStream,
  runsApi,
  sessionsApi,
  StreamAdmissionError,
} from "@/services";
import type { AttachmentDraft, FriendlyError } from "@/services";
import type { ChatAttachment, ChatMessage, TextBlock } from "@/types/chat";
import type { SessionDetail } from "@/types/meta";
import type { PushEvent } from "@/types/push";
import type { RunSnapshot } from "@/types/run";
import type { SearchNavigationTarget, SessionSearchResult } from "@/types/search";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { useSessionsStore } from "./sessions";
import { HOME_DRAFT_KEY, useDraftStore } from "./drafts";
import { createStreamUpdateScheduler } from "./streamUpdateScheduler";

/** 前端本地网络/HTTP 失败兜底；RUN_ERROR 用 bridge 按错误码清洗后的文案。 */
const FRIENDLY_FALLBACK = "这轮回答中断了，没有生成完整回复。请稍后重试。";

interface RunTransport {
  sessionId: string;
  controller: AbortController;
}

const activeTransports = new Map<string, RunTransport>();
const HOME_VIEW_KEY = "__agent_friend_home__";
const MAX_IDLE_SESSION_VIEWS = 20;
const MAX_RUN_SNAPSHOTS = 512;
let openSessionToken = 0;
let detailRefreshSeq = 0;
let modelMutationSeq = 0;
let searchNavigationToken = 0;

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

interface ConversationState {
  currentSessionId: string | null;
  currentModel: string | null;
  modelSwitching: boolean;
  modelSwitchTarget: string | null;
  editingMessageId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  historyLoading: boolean;
  /** 历史会话加载完成事件序号；UI 用它触发一次 post-layout 滚动校正。 */
  historyLoadSeq: number;
  /** 后台同步完成序号；UI 用它保护阅读锚点，不触发历史首次加载滚底。 */
  syncRefreshSeq: number;
  sessionViews: Record<string, SessionConversationView>;
  runSnapshots: Record<string, RunSnapshot>;
  viewAccessedAt: Record<string, number>;
  activeRunIdBySession: Record<string, string>;
  unreadRunOutcomeBySession: Record<string, "completed" | "failed">;
  detachedSessionId: string | null;
  searchTarget: SearchNavigationTarget | null;
  /** 开一个新会话（清空当前视图；session 在首次 send 时由 bridge 自动创建）。 */
  newSession: () => void;
  /** 切换到历史会话并加载其上下文。 */
  openSession: (sessionId: string, options?: OpenSessionOptions) => Promise<boolean>;
  openSearchResult: (result: SessionSearchResult, query: string) => Promise<boolean>;
  clearSearchTarget: (token: number) => void;
  refreshCurrentSession: (sessionId: string) => Promise<"applied" | "stale" | "failed">;
  invalidateBackgroundRefreshes: () => void;
  applyRunEvents: (sessionId: string, runId: string, events: PushEvent[]) => void;
  applyRunSnapshot: (snapshot: RunSnapshot) => void;
  calibrateRuns: () => Promise<void>;
  markSessionViewed: (sessionId: string) => void;
  pruneCaches: () => void;
  selectModel: (modelId: string) => Promise<ModelSelectionOutcome>;
  setEditingMessage: (messageId: string | null) => void;
  /** 接纳一条消息并启动流式回复；接纳结果与整轮完成结果分开返回。 */
  send: (text: string, attachments?: AttachmentDraft[]) => SendStartResult;
  /** 编辑并重发最后一条用户消息。 */
  editResendLatest: (messageId: string, text: string) => Promise<void>;
  /** 不重跑工具，复用失败轮中已经完成的工具结果继续生成。 */
  continueToolResult: (messageId: string) => Promise<void>;
  /** 打断当前流（保留已生成的部分）。 */
  stop: () => void;
}

interface SessionConversationView {
  messages: ChatMessage[];
  currentModel: string | null;
  streaming: boolean;
  historyLoading: boolean;
  historyLoadSeq: number;
  syncRefreshSeq: number;
  revision: number;
}

export type SendOutcome = "completed" | "failed" | "interrupted" | "rejected";

export type SendStartResult =
  | {
      accepted: false;
      reason: "empty" | "busy" | "history-loading" | "model-switching" | "editing";
    }
  | {
      accepted: true;
      sessionId: string;
      runId: string;
      completion: Promise<SendOutcome>;
    };

export type ModelSelectionOutcome =
  | { status: "applied"; model: string }
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

function newId(): string {
  return crypto.randomUUID();
}

function userMessage(
  text: string,
  createdAt = new Date().toISOString(),
  attachments: ChatAttachment[] = [],
  id = newId(),
): ChatMessage {
  const block: TextBlock = { kind: "text", mid: id, text };
  return {
    id,
    role: "user",
    createdAt,
    blocks: text ? [block] : [],
    attachments,
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
    streaming: state.streaming,
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
    streaming: false,
    historyLoading: false,
    historyLoadSeq: 0,
    syncRefreshSeq: 0,
    revision: 0,
  };
}

function viewFields(view: SessionConversationView) {
  return {
    messages: view.messages,
    currentModel: view.currentModel,
    streaming: view.streaming,
    historyLoading: view.historyLoading,
    historyLoadSeq: view.historyLoadSeq,
    syncRefreshSeq: view.syncRefreshSeq,
  };
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  currentSessionId: null,
  currentModel: null,
  modelSwitching: false,
  modelSwitchTarget: null,
  editingMessageId: null,
  messages: [],
  streaming: false,
  historyLoading: false,
  historyLoadSeq: 0,
  syncRefreshSeq: 0,
  sessionViews: {},
  runSnapshots: {},
  viewAccessedAt: {},
  activeRunIdBySession: {},
  unreadRunOutcomeBySession: {},
  detachedSessionId: null,
  searchTarget: null,

  newSession() {
    openSessionToken += 1;
    detailRefreshSeq += 1;
    modelMutationSeq += 1;
    searchNavigationToken += 1;
    rememberLastChatSession(null);
    set((state) => {
      const previousKey = sessionViewKey(state.currentSessionId);
      const home = state.sessionViews[HOME_VIEW_KEY] ?? emptyView();
      return {
        ...viewFields(home),
        currentSessionId: null,
        modelSwitching: false,
        modelSwitchTarget: null,
        editingMessageId: null,
        detachedSessionId: null,
        searchTarget: null,
        sessionViews: {
          ...state.sessionViews,
          [previousKey]: currentViewSnapshot(state),
        },
        viewAccessedAt: { ...state.viewAccessedAt, [HOME_VIEW_KEY]: Date.now() },
      };
    });
    get().pruneCaches();
  },

  async openSession(sessionId, options) {
    const failureMode = options?.failureMode ?? "message";
    const remember = options?.remember ?? true;
    const detached = options?.detached ?? false;
    const previousSessionId = get().currentSessionId;
    const previousDetachedSessionId = get().detachedSessionId;
    const token = openSessionToken + 1;
    openSessionToken = token;
    detailRefreshSeq += 1;
    modelMutationSeq += 1;
    if (remember) rememberLastChatSession(sessionId);
    const cached = get().sessionViews[sessionId];
    const hasActiveRun = Boolean(get().activeRunIdBySession[sessionId]);
    set((state) => ({
      ...viewFields(
        cached
          ? { ...cached, streaming: cached.streaming || hasActiveRun }
          : { ...emptyView(), streaming: hasActiveRun, historyLoading: true },
      ),
      currentSessionId: sessionId,
      historyLoading: cached?.streaming ? false : true,
      modelSwitching: false,
      modelSwitchTarget: null,
      editingMessageId: null,
      detachedSessionId: detached ? sessionId : null,
      searchTarget: options?.searchTarget ?? null,
      sessionViews: {
        ...state.sessionViews,
        [sessionViewKey(state.currentSessionId)]: currentViewSnapshot(state),
      },
      viewAccessedAt: { ...state.viewAccessedAt, [sessionId]: Date.now() },
    }));
    if (cached?.streaming) {
      get().markSessionViewed(sessionId);
      get().pruneCaches();
      return true;
    }
    try {
      const detail = await sessionsApi.get(sessionId);
      if (token !== openSessionToken || get().currentSessionId !== sessionId) return false;
      set((s) => {
        const view = {
          messages: projectSessionDetail(detail),
          currentModel: detail.model,
          streaming: Boolean(s.activeRunIdBySession[sessionId]),
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
      if (token !== openSessionToken || get().currentSessionId !== sessionId) return false;
      if (failureMode === "home") {
        set((s) => {
          const home = s.sessionViews[HOME_VIEW_KEY] ?? emptyView();
          return {
            ...viewFields(home),
            currentSessionId: null,
            modelSwitching: false,
            modelSwitchTarget: null,
            editingMessageId: null,
            detachedSessionId: null,
            searchTarget: null,
            historyLoadSeq: s.historyLoadSeq + 1,
          };
        });
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
            editingMessageId: null,
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
    if (get().currentSessionId !== sessionId || get().streaming) return "stale";
    try {
      const detail = await sessionsApi.get(sessionId);
      if (
        seq !== detailRefreshSeq ||
        openToken !== openSessionToken ||
        get().currentSessionId !== sessionId ||
        get().streaming
      ) {
        return "stale";
      }
      const revision = detail.revision ?? 0;
      if ((get().sessionViews[sessionId]?.revision ?? 0) > revision) return "stale";
      set((state) => {
        const view: SessionConversationView = {
          messages: projectSessionDetail(detail),
          currentModel: detail.model,
          streaming: false,
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
      return seq === detailRefreshSeq ? "failed" : "stale";
    }
  },

  invalidateBackgroundRefreshes() {
    detailRefreshSeq += 1;
  },

  applyRunEvents(sessionId, runId, events) {
    if (events.length === 0) return;
    set((state) => {
      const activeRunId = state.activeRunIdBySession[sessionId];
      if (activeRunId && activeRunId !== runId) return state;

      const isCurrent = state.currentSessionId === sessionId;
      const existingView = state.sessionViews[sessionId];
      const view = existingView ?? (isCurrent ? currentViewSnapshot(state) : undefined);
      if (!view) return state;

      const assistantId = `proactive:${runId}`;
      const existingIndex = view.messages.findIndex((message) => message.id === assistantId);
      const assistant: ChatMessage = existingIndex >= 0
        ? view.messages[existingIndex]
        : {
            id: assistantId,
            role: "assistant",
            createdAt: new Date().toISOString(),
            blocks: [],
            status: "streaming",
          };
      const nextAssistant = events.reduce(
        (message, event) => applyPushConversationEvent(message, event, runId),
        assistant,
      );
      const messages = [...view.messages];
      if (existingIndex >= 0) messages[existingIndex] = nextAssistant;
      else messages.push(nextAssistant);
      const nextView: SessionConversationView = { ...view, messages, streaming: true };

      return {
        sessionViews: { ...state.sessionViews, [sessionId]: nextView },
        activeRunIdBySession: { ...state.activeRunIdBySession, [sessionId]: runId },
        ...(isCurrent ? { messages, streaming: true } : {}),
      };
    });
  },

  applyRunSnapshot(snapshot) {
    const previous = get().runSnapshots[snapshot.run_id];
    if (previous && previous.version >= snapshot.version) return;
    set((state) => {
      const terminal = snapshot.status === "terminal";
      const activeRunIdBySession = { ...state.activeRunIdBySession };
      if (terminal) {
        if (activeRunIdBySession[snapshot.session_id] === snapshot.run_id) {
          delete activeRunIdBySession[snapshot.session_id];
        }
      } else {
        activeRunIdBySession[snapshot.session_id] = snapshot.run_id;
      }
      const view = state.sessionViews[snapshot.session_id];
      const nextView = view
        ? { ...view, streaming: !terminal }
        : view;
      const isCurrent = state.currentSessionId === snapshot.session_id;
      const unreadRunOutcomeBySession = { ...state.unreadRunOutcomeBySession };
      if (terminal && !isCurrent) {
        unreadRunOutcomeBySession[snapshot.session_id] =
          snapshot.outcome === "completed" ? "completed" : "failed";
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
      return {
        runSnapshots,
        activeRunIdBySession,
        unreadRunOutcomeBySession,
        sessionViews: nextView
          ? { ...state.sessionViews, [snapshot.session_id]: nextView }
          : state.sessionViews,
        streaming: isCurrent ? !terminal : state.streaming,
      };
    });
    if (snapshot.status === "terminal") {
      activeTransports.delete(snapshot.run_id);
      if (get().currentSessionId === snapshot.session_id) {
        void get().refreshCurrentSession(snapshot.session_id);
      }
    }
    get().pruneCaches();
  },

  async calibrateRuns() {
    const response = await runsApi.list(true);
    for (const snapshot of response.runs) get().applyRunSnapshot(snapshot);
    const returnedRunIds = new Set(response.runs.map((snapshot) => snapshot.run_id));
    const sessionsToRefresh: string[] = [];
    set((state) => {
      const activeRunIdBySession = { ...state.activeRunIdBySession };
      const sessionViews = { ...state.sessionViews };
      for (const [sessionId, runId] of Object.entries(activeRunIdBySession)) {
        if (returnedRunIds.has(runId)) continue;
        delete activeRunIdBySession[sessionId];
        const view = sessionViews[sessionId];
        if (view) sessionViews[sessionId] = { ...view, streaming: false };
        sessionsToRefresh.push(sessionId);
      }
      return {
        activeRunIdBySession,
        sessionViews,
        streaming:
          state.currentSessionId && sessionsToRefresh.includes(state.currentSessionId)
            ? false
            : state.streaming,
      };
    });
    const currentSessionId = get().currentSessionId;
    if (currentSessionId && sessionsToRefresh.includes(currentSessionId)) {
      void get().refreshCurrentSession(currentSessionId);
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
        ...Object.keys(state.activeRunIdBySession),
      ]);
      for (const [key, draft] of Object.entries(draftWorkspaces)) {
        if (draft.text || draft.attachments.length > 0) {
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
    const current = get();
    const effectiveModel = current.currentModel ?? resolveDefaultConversationModel();
    if (modelId === effectiveModel) return { status: "applied", model: effectiveModel };
    if (current.streaming || current.historyLoading || current.editingMessageId) {
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
    set({ modelSwitching: true, modelSwitchTarget: modelId });
    try {
      const appliedModel = sessionId
        ? (await sessionsApi.switchModel(sessionId, modelId)).model
        : (await modelsApi.validate(modelId)).model;
      if (
        seq !== modelMutationSeq ||
        openToken !== openSessionToken ||
        get().currentSessionId !== sessionId
      ) {
        return { status: "stale" };
      }
      set({ currentModel: appliedModel });
      return { status: "applied", model: appliedModel };
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

  setEditingMessage(messageId) {
    set({ editingMessageId: messageId });
  },

  send(text, attachments = []) {
    const trimmed = text.trim();
    if (!trimmed && attachments.length === 0) return { accepted: false, reason: "empty" };
    if (get().streaming) return { accepted: false, reason: "busy" };
    if (get().historyLoading) return { accepted: false, reason: "history-loading" };
    if (get().modelSwitching) return { accepted: false, reason: "model-switching" };
    if (get().editingMessageId) return { accepted: false, reason: "editing" };

    const sessionId = get().currentSessionId ?? newId();
    const runId = newId();
    const initialModel = get().currentModel ?? resolveDefaultConversationModel();
    const assistantId = newId();
    const sentAt = new Date().toISOString();
    const userMessageId = newId();
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
      createdAt: sentAt,
      blocks: [],
      status: "streaming",
    };
    set((s) => {
      const messages = [
        ...s.messages,
        userMessage(trimmed, sentAt, chatAttachments, userMessageId),
        placeholder,
      ];
      const view: SessionConversationView = {
        messages,
        currentModel: initialModel,
        streaming: true,
        historyLoading: false,
        historyLoadSeq: s.historyLoadSeq,
        syncRefreshSeq: s.syncRefreshSeq,
        revision: s.sessionViews[sessionId]?.revision ?? 0,
      };
      return {
        currentSessionId: sessionId,
        currentModel: initialModel,
        streaming: true,
        messages,
        sessionViews: {
          ...s.sessionViews,
          [sessionId]: view,
          ...(s.currentSessionId === null ? { [HOME_VIEW_KEY]: emptyView() } : {}),
        },
        activeRunIdBySession: { ...s.activeRunIdBySession, [sessionId]: runId },
        viewAccessedAt: { ...s.viewAccessedAt, [sessionId]: Date.now() },
      };
    });
    rememberLastChatSession(sessionId);

    const controller = new AbortController();
    activeTransports.set(runId, { sessionId, controller });

    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set((s) => {
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
    const streamUpdates = createStreamUpdateScheduler((events) => {
      patch((m) => applyStreamEvents(m, events));
    });

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
            model: initialModel,
            text: trimmed,
            messageId: userMessageId,
            attachments,
          },
          { signal: controller.signal },
        )) {
          sawStreamEvent = true;
          if (evt.type === EventType.RUN_FINISHED || evt.type === EventType.RUN_ERROR) {
            sawTerminalEvent = true;
          }
          streamUpdates.enqueue(evt);
        }
        streamUpdates.flush();
        patch((m) => (m.status === "streaming" ? { ...m, status: "complete" } : m));
        outcome = get().sessionViews[sessionId]?.messages
          .find((message) => message.id === assistantId)?.status === "error"
          ? "failed"
          : "completed";
      } catch (error) {
        streamUpdates.flush();
        admissionFailed = error instanceof StreamAdmissionError;
        if (controller.signal.aborted) {
          outcome = "interrupted";
          patch((m) => (m.status === "streaming" ? { ...m, status: "interrupted" } : m));
        } else {
          patch((m) => ({ ...m, status: "error", error: FRIENDLY_FALLBACK }));
        }
      } finally {
        streamUpdates.dispose();
        activeTransports.delete(runId);
        let runStillActive = false;
        try {
          const snapshot = await runsApi.get(runId);
          get().applyRunSnapshot(snapshot);
          runStillActive = snapshot.status !== "terminal";
          if (snapshot.outcome === "cancelled") outcome = "interrupted";
          else if (snapshot.outcome === "rejected") outcome = "rejected";
        } catch {
          // push/reconnect calibration remains the fallback when the transport vanished early.
          if (!sawStreamEvent && admissionFailed) outcome = "rejected";
          runStillActive = !sawTerminalEvent && !admissionFailed;
        }
        set((s) => {
          const existing = s.sessionViews[sessionId];
          if (!existing) return s;
          const next = {
            ...existing,
            streaming: runStillActive,
            messages:
              outcome === "rejected"
                ? existing.messages.filter(
                    (message) =>
                      message.id !== userMessageId && message.id !== assistantId,
                  )
                : existing.messages,
          };
          const activeRunIdBySession = { ...s.activeRunIdBySession };
          if (!runStillActive && activeRunIdBySession[sessionId] === runId) {
            delete activeRunIdBySession[sessionId];
          }
          return {
            sessionViews: { ...s.sessionViews, [sessionId]: next },
            activeRunIdBySession,
            streaming: s.currentSessionId === sessionId ? runStillActive : s.streaming,
            messages: s.currentSessionId === sessionId ? next.messages : s.messages,
          };
        });
        void useSessionsStore.getState().refresh();
      }
      return outcome;
    })();

    return { accepted: true, sessionId, runId, completion };
  },

  async editResendLatest(messageId, text) {
    const trimmed = text.trim();
    const state = get();
    if (
      !trimmed ||
      state.streaming ||
      state.historyLoading ||
      state.modelSwitching ||
      !state.currentSessionId
    ) return;

    const latestUserIndex = [...state.messages]
      .reverse()
      .findIndex((m) => m.role === "user");
    if (latestUserIndex === -1) return;
    const targetIndex = state.messages.length - 1 - latestUserIndex;
    const target = state.messages[targetIndex];
    if (!target || target.id !== messageId || target.role !== "user") return;

    const sessionId = state.currentSessionId;
    const runId = newId();
    const expectedUserContent = messageText(target);
    const assistantId = newId();
    const sentAt = new Date().toISOString();
    const placeholder: ChatMessage = {
      id: assistantId,
      role: "assistant",
      createdAt: sentAt,
      blocks: [],
      status: "streaming",
    };

    const messages = [
        ...state.messages.slice(0, targetIndex),
        userMessage(trimmed, sentAt, target.attachments ?? []),
        placeholder,
      ];
    set((current) => ({
      editingMessageId: null,
      streaming: true,
      messages,
      sessionViews: {
        ...current.sessionViews,
        [sessionId]: {
          ...(current.sessionViews[sessionId] ?? emptyView()),
          messages,
          streaming: true,
        },
      },
      activeRunIdBySession: {
        ...current.activeRunIdBySession,
        [sessionId]: runId,
      },
    }));

    const controller = new AbortController();
    activeTransports.set(runId, { sessionId, controller });

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
    const streamUpdates = createStreamUpdateScheduler((events) => {
      patch((m) => applyStreamEvents(m, events));
    });

    let failedError: string | null = null;
    try {
      for await (const evt of editResendLatestStream(
        { sessionId, runId, text: trimmed, expectedUserContent },
        { signal: controller.signal },
      )) {
        streamUpdates.enqueue(evt);
      }
      streamUpdates.flush();
      const assistant = get().sessionViews[sessionId]?.messages.find(
        (m) => m.id === assistantId,
      );
      if (assistant?.status === "error") {
        failedError = assistant.error ?? FRIENDLY_FALLBACK;
      } else {
        patch((m) => (m.status === "streaming" ? { ...m, status: "complete" } : m));
      }
    } catch {
      streamUpdates.flush();
      if (controller.signal.aborted) {
        patch((m) => (m.status === "streaming" ? { ...m, status: "interrupted" } : m));
      } else {
        failedError = FRIENDLY_FALLBACK;
        patch((m) => ({ ...m, status: "error", error: FRIENDLY_FALLBACK }));
      }
    } finally {
      streamUpdates.dispose();
      activeTransports.delete(runId);
      try {
        get().applyRunSnapshot(await runsApi.get(runId));
      } catch {
        // run_changed / reconnect calibration remains the fallback.
      }
      set((s) => {
        const view = s.sessionViews[sessionId];
        if (!view) return s;
        const next = { ...view, streaming: false };
        const activeRunIdBySession = { ...s.activeRunIdBySession };
        if (activeRunIdBySession[sessionId] === runId) delete activeRunIdBySession[sessionId];
        return {
          sessionViews: { ...s.sessionViews, [sessionId]: next },
          activeRunIdBySession,
          streaming: s.currentSessionId === sessionId ? false : s.streaming,
        };
      });
      try {
        const detail = await sessionsApi.get(sessionId);
        const projected = projectSessionDetail(detail);
        set((s) => {
          const view = s.sessionViews[sessionId] ?? emptyView();
          const nextMessages = failedError
            ? hasErrorMessage(projected, failedError)
              ? projected
              : [...projected, assistantErrorMessage(failedError)]
            : projected;
          const nextView = {
            ...view,
            messages: nextMessages,
            currentModel: detail.model,
            streaming: false,
            revision: detail.revision ?? view.revision,
            historyLoadSeq: view.historyLoadSeq + 1,
          };
          return {
            sessionViews: { ...s.sessionViews, [sessionId]: nextView },
            ...(s.currentSessionId === sessionId
              ? { ...viewFields(nextView), currentModel: detail.model }
              : {}),
          };
        });
      } catch {
        // 保留乐观态 + 错误占位，避免二次失败把用户带回空白。
      }
      void useSessionsStore.getState().refresh();
    }
  },

  async continueToolResult(messageId) {
    const state = get();
    const target = state.messages.at(-1);
    if (
      state.streaming ||
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
      createdAt: new Date().toISOString(),
      blocks: [],
      status: "streaming",
    };
    const messages = [...state.messages, placeholder];
    set((current) => ({
      streaming: true,
      messages,
      sessionViews: {
        ...current.sessionViews,
        [sessionId]: {
          ...(current.sessionViews[sessionId] ?? emptyView()),
          messages,
          streaming: true,
        },
      },
      activeRunIdBySession: {
        ...current.activeRunIdBySession,
        [sessionId]: runId,
      },
    }));

    const controller = new AbortController();
    activeTransports.set(runId, { sessionId, controller });
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
    const streamUpdates = createStreamUpdateScheduler((events) => {
      patch((m) => applyStreamEvents(m, events));
    });

    try {
      for await (const evt of continueToolResultStream(sessionId, runId, {
        signal: controller.signal,
      })) {
        streamUpdates.enqueue(evt);
      }
      streamUpdates.flush();
      patch((m) => (m.status === "streaming" ? { ...m, status: "complete" } : m));
    } catch {
      streamUpdates.flush();
      if (controller.signal.aborted) {
        patch((m) => (m.status === "streaming" ? { ...m, status: "interrupted" } : m));
      } else {
        patch((m) => ({ ...m, status: "error", error: FRIENDLY_FALLBACK }));
      }
    } finally {
      streamUpdates.dispose();
      activeTransports.delete(runId);
      try {
        get().applyRunSnapshot(await runsApi.get(runId));
      } catch {
        // run_changed / reconnect calibration remains the fallback.
      }
      set((s) => {
        const view = s.sessionViews[sessionId];
        if (!view) return s;
        const next = { ...view, streaming: false };
        const activeRunIdBySession = { ...s.activeRunIdBySession };
        if (activeRunIdBySession[sessionId] === runId) delete activeRunIdBySession[sessionId];
        return {
          sessionViews: { ...s.sessionViews, [sessionId]: next },
          activeRunIdBySession,
          streaming: s.currentSessionId === sessionId ? false : s.streaming,
        };
      });
      void useSessionsStore.getState().refresh();
    }
  },

  stop() {
    const sessionId = get().currentSessionId;
    if (!sessionId) return;
    const runId = get().activeRunIdBySession[sessionId];
    if (!runId) return;
    void runsApi.cancel(sessionId, runId).then(({ run }) => {
      get().applyRunSnapshot(run);
    }).catch(() => {
      void get().calibrateRuns();
    });
  },
}));
