/**
 * 当前会话 store（design §4.3/§3.5）：消息列表 + 流式态，消费 AG-UI 事件流。
 *
 * - `send`：乐观插入 user + 占位 assistant，自写 fetch-SSE 消费、reducer 累积。
 * - `openSession`：拉历史事件投影成消息，续聊用同一 session_id 作 threadId。
 * - 错误统一为 turn 状态，不暴露技术细节。
 */

import { create } from "zustand";
import { setLastChatSessionId } from "@/lib/persistence/chatUi";
import { resolveDefaultConversationModel } from "@/lib/model-config";
import { editResendLatestStream, modelsApi, runAgentStream, sessionsApi } from "@/services";
import type { AttachmentDraft, FriendlyError } from "@/services";
import type { ChatAttachment, ChatMessage, TextBlock } from "@/types/chat";
import { applyAguiEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { useSessionsStore } from "./sessions";
import { createStreamUpdateScheduler } from "./streamUpdateScheduler";

/** 前端本地网络/HTTP 失败兜底；RUN_ERROR 用 bridge 按错误码清洗后的文案。 */
const FRIENDLY_FALLBACK = "这轮回答中断了，没有生成完整回复。请稍后重试。";

/** 本窗口同一时刻只允许一条进行中的流。 */
let activeController: AbortController | null = null;
let openSessionToken = 0;
let detailRefreshSeq = 0;
let modelMutationSeq = 0;

type OpenSessionFailureMode = "message" | "home";

interface OpenSessionOptions {
  /** 自动恢复失败时用 home，手动打开失败时沿用 message。 */
  failureMode?: OpenSessionFailureMode;
  /** 自动恢复读取的是已有持久化指针，不需要重复写入。 */
  remember?: boolean;
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
  /** 开一个新会话（清空当前视图；session 在首次 send 时由 bridge 自动创建）。 */
  newSession: () => void;
  /** 切换到历史会话并加载其上下文。 */
  openSession: (sessionId: string, options?: OpenSessionOptions) => Promise<boolean>;
  refreshCurrentSession: (sessionId: string) => Promise<"applied" | "stale" | "failed">;
  invalidateBackgroundRefreshes: () => void;
  selectModel: (modelId: string) => Promise<ModelSelectionOutcome>;
  setEditingMessage: (messageId: string | null) => void;
  /** 接纳一条消息并启动流式回复；接纳结果与整轮完成结果分开返回。 */
  send: (text: string, attachments?: AttachmentDraft[]) => SendStartResult;
  /** 编辑并重发最后一条用户消息。 */
  editResendLatest: (messageId: string, text: string) => Promise<void>;
  /** 打断当前流（保留已生成的部分）。 */
  stop: () => void;
}

export type SendOutcome = "completed" | "failed" | "interrupted";

export type SendStartResult =
  | {
      accepted: false;
      reason: "empty" | "busy" | "history-loading" | "model-switching" | "editing";
    }
  | { accepted: true; completion: Promise<SendOutcome> };

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

function projectSessionDetail(detail: { events: Parameters<typeof projectSessionEvents>[0]; active_events?: Parameters<typeof projectSessionEvents>[0] }) {
  return projectSessionEvents(detail.active_events ?? detail.events);
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

  newSession() {
    openSessionToken += 1;
    detailRefreshSeq += 1;
    modelMutationSeq += 1;
    if (get().streaming) get().stop();
    rememberLastChatSession(null);
    set({
      currentSessionId: null,
      currentModel: null,
      modelSwitching: false,
      modelSwitchTarget: null,
      editingMessageId: null,
      messages: [],
      historyLoading: false,
    });
  },

  async openSession(sessionId, options) {
    const failureMode = options?.failureMode ?? "message";
    const remember = options?.remember ?? true;
    const token = openSessionToken + 1;
    openSessionToken = token;
    detailRefreshSeq += 1;
    modelMutationSeq += 1;
    if (get().streaming) get().stop();
    if (remember) rememberLastChatSession(sessionId);
    set({
      currentSessionId: sessionId,
      currentModel: null,
      modelSwitching: false,
      modelSwitchTarget: null,
      editingMessageId: null,
      messages: [],
      historyLoading: true,
    });
    try {
      const detail = await sessionsApi.get(sessionId);
      if (token !== openSessionToken || get().currentSessionId !== sessionId) return false;
      set((s) => ({
        messages: projectSessionDetail(detail),
        currentModel: detail.model,
        historyLoading: false,
        historyLoadSeq: s.historyLoadSeq + 1,
      }));
      return true;
    } catch {
      if (token !== openSessionToken || get().currentSessionId !== sessionId) return false;
      if (failureMode === "home") {
        set((s) => ({
          currentSessionId: null,
          currentModel: null,
          modelSwitching: false,
          modelSwitchTarget: null,
          editingMessageId: null,
          messages: [],
          historyLoading: false,
          historyLoadSeq: s.historyLoadSeq + 1,
        }));
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
      set((state) => ({
        messages: projectSessionDetail(detail),
        currentModel: detail.model,
        syncRefreshSeq: state.syncRefreshSeq + 1,
      }));
      return "applied";
    } catch {
      return seq === detailRefreshSeq ? "failed" : "stale";
    }
  },

  invalidateBackgroundRefreshes() {
    detailRefreshSeq += 1;
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
    set((s) => ({
      currentSessionId: sessionId,
      currentModel: initialModel,
      streaming: true,
      messages: [
        ...s.messages,
        userMessage(trimmed, sentAt, chatAttachments, userMessageId),
        placeholder,
      ],
    }));
    rememberLastChatSession(sessionId);

    const controller = new AbortController();
    activeController = controller;

    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set((s) => ({
        messages: s.messages.map((m) => (m.id === assistantId ? fn(m) : m)),
      }));
    const streamUpdates = createStreamUpdateScheduler((events) => {
      patch((m) => applyStreamEvents(m, events));
    });

    const completion = (async (): Promise<SendOutcome> => {
      let outcome: SendOutcome = "failed";
      try {
        for await (const evt of runAgentStream(
          {
            threadId: sessionId,
            model: initialModel,
            text: trimmed,
            messageId: userMessageId,
            attachments,
          },
          { signal: controller.signal },
        )) {
          streamUpdates.enqueue(evt);
        }
        streamUpdates.flush();
        patch((m) => (m.status === "streaming" ? { ...m, status: "complete" } : m));
        outcome = get().messages.find((message) => message.id === assistantId)?.status === "error"
          ? "failed"
          : "completed";
      } catch {
        streamUpdates.flush();
        if (controller.signal.aborted) {
          outcome = "interrupted";
          patch((m) => (m.status === "streaming" ? { ...m, status: "interrupted" } : m));
        } else {
          patch((m) => ({ ...m, status: "error", error: FRIENDLY_FALLBACK }));
        }
      } finally {
        streamUpdates.dispose();
        if (activeController === controller) activeController = null;
        set({ streaming: false });
        void useSessionsStore.getState().refresh();
      }
      return outcome;
    })();

    return { accepted: true, completion };
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

    set({
      editingMessageId: null,
      streaming: true,
      messages: [
        ...state.messages.slice(0, targetIndex),
        userMessage(trimmed, sentAt, target.attachments ?? []),
        placeholder,
      ],
    });

    const controller = new AbortController();
    activeController = controller;

    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set((s) => ({
        messages: s.messages.map((m) => (m.id === assistantId ? fn(m) : m)),
      }));
    const streamUpdates = createStreamUpdateScheduler((events) => {
      patch((m) => applyStreamEvents(m, events));
    });

    let failedError: string | null = null;
    try {
      for await (const evt of editResendLatestStream(
        { sessionId, text: trimmed, expectedUserContent },
        { signal: controller.signal },
      )) {
        streamUpdates.enqueue(evt);
      }
      streamUpdates.flush();
      const assistant = get().messages.find((m) => m.id === assistantId);
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
      if (activeController === controller) activeController = null;
      set({ streaming: false });
      if (get().currentSessionId === sessionId) {
        try {
          const detail = await sessionsApi.get(sessionId);
          if (get().currentSessionId === sessionId) {
            const projected = projectSessionDetail(detail);
            set((s) => ({
              messages: failedError
                ? hasErrorMessage(projected, failedError)
                  ? projected
                  : [...projected, assistantErrorMessage(failedError)]
                : projected,
              currentModel: detail.model,
              historyLoadSeq: s.historyLoadSeq + 1,
            }));
          }
        } catch {
          // 保留乐观态 + 错误占位，避免二次失败把用户带回空白。
        }
      }
      void useSessionsStore.getState().refresh();
    }
  },

  stop() {
    activeController?.abort();
  },
}));
