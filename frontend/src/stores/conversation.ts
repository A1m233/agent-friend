/**
 * 当前会话 store（design §4.3/§3.5）：消息列表 + 流式态，消费 AG-UI 事件流。
 *
 * - `send`：乐观插入 user + 占位 assistant，自写 fetch-SSE 消费、reducer 累积。
 * - `openSession`：拉历史事件投影成消息，续聊用同一 session_id 作 threadId。
 * - 错误统一兜底为拟人文案（R-M3.6），不暴露技术细节。
 */

import { create } from "zustand";
import { setLastChatSessionId } from "@/lib/persistence/chatUi";
import { runAgentStream, sessionsApi } from "@/services";
import type { ChatMessage, TextBlock } from "@/types/chat";
import { applyAguiEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { useSessionsStore } from "./sessions";

/** 流式失败（网络/HTTP）时的拟人兜底；RUN_ERROR 用 bridge 下发的文案。 */
const FRIENDLY_FALLBACK = "我这边好像走神了一下，待会儿再聊好吗？";

/** 本窗口同一时刻只允许一条进行中的流。 */
let activeController: AbortController | null = null;
let openSessionToken = 0;

type OpenSessionFailureMode = "message" | "home";

interface OpenSessionOptions {
  /** 自动恢复失败时用 home，手动打开失败时沿用 message。 */
  failureMode?: OpenSessionFailureMode;
  /** 自动恢复读取的是已有持久化指针，不需要重复写入。 */
  remember?: boolean;
}

interface ConversationState {
  currentSessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  historyLoading: boolean;
  /** 历史会话加载完成事件序号；UI 用它触发一次 post-layout 滚动校正。 */
  historyLoadSeq: number;
  /** 开一个新会话（清空当前视图；session 在首次 send 时由 bridge 自动创建）。 */
  newSession: () => void;
  /** 切换到历史会话并加载其上下文。 */
  openSession: (sessionId: string, options?: OpenSessionOptions) => Promise<boolean>;
  /** 发送一条消息并消费流式回复。 */
  send: (text: string) => Promise<void>;
  /** 打断当前流（保留已生成的部分）。 */
  stop: () => void;
}

function newId(): string {
  return crypto.randomUUID();
}

function userMessage(text: string, createdAt = new Date().toISOString()): ChatMessage {
  const id = newId();
  const block: TextBlock = { kind: "text", mid: id, text };
  return { id, role: "user", createdAt, blocks: [block], status: "complete" };
}

function rememberLastChatSession(sessionId: string | null): void {
  void setLastChatSessionId(sessionId).catch(() => {
    // UI 恢复状态写入失败不影响当前对话；后续有统一 telemetry 再上报。
  });
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  currentSessionId: null,
  messages: [],
  streaming: false,
  historyLoading: false,
  historyLoadSeq: 0,

  newSession() {
    openSessionToken += 1;
    if (get().streaming) get().stop();
    rememberLastChatSession(null);
    set({ currentSessionId: null, messages: [], historyLoading: false });
  },

  async openSession(sessionId, options) {
    const failureMode = options?.failureMode ?? "message";
    const remember = options?.remember ?? true;
    const token = openSessionToken + 1;
    openSessionToken = token;
    if (get().streaming) get().stop();
    if (remember) rememberLastChatSession(sessionId);
    set({ currentSessionId: sessionId, messages: [], historyLoading: true });
    try {
      const detail = await sessionsApi.get(sessionId);
      if (token !== openSessionToken || get().currentSessionId !== sessionId) return false;
      set((s) => ({
        messages: projectSessionEvents(detail.events),
        historyLoading: false,
        historyLoadSeq: s.historyLoadSeq + 1,
      }));
      return true;
    } catch {
      if (token !== openSessionToken || get().currentSessionId !== sessionId) return false;
      if (failureMode === "home") {
        set((s) => ({
          currentSessionId: null,
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

  async send(text) {
    const trimmed = text.trim();
    if (!trimmed || get().streaming || get().historyLoading) return;

    const sessionId = get().currentSessionId ?? newId();
    const assistantId = newId();
    const sentAt = new Date().toISOString();
    const placeholder: ChatMessage = {
      id: assistantId,
      role: "assistant",
      createdAt: sentAt,
      blocks: [],
      status: "streaming",
    };
    set((s) => ({
      currentSessionId: sessionId,
      streaming: true,
      messages: [...s.messages, userMessage(trimmed, sentAt), placeholder],
    }));
    rememberLastChatSession(sessionId);

    const controller = new AbortController();
    activeController = controller;

    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      set((s) => ({
        messages: s.messages.map((m) => (m.id === assistantId ? fn(m) : m)),
      }));

    try {
      for await (const evt of runAgentStream(
        { threadId: sessionId, text: trimmed },
        { signal: controller.signal },
      )) {
        patch((m) => applyAguiEvent(m, evt));
      }
      patch((m) => (m.status === "streaming" ? { ...m, status: "complete" } : m));
    } catch {
      if (controller.signal.aborted) {
        patch((m) => (m.status === "streaming" ? { ...m, status: "complete" } : m));
      } else {
        patch((m) => ({ ...m, status: "error", error: FRIENDLY_FALLBACK }));
      }
    } finally {
      if (activeController === controller) activeController = null;
      set({ streaming: false });
      void useSessionsStore.getState().refresh();
    }
  },

  stop() {
    activeController?.abort();
  },
}));
