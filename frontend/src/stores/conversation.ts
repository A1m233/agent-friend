/**
 * 当前会话 store（design §4.3/§3.5）：消息列表 + 流式态，消费 AG-UI 事件流。
 *
 * - `send`：乐观插入 user + 占位 assistant，自写 fetch-SSE 消费、reducer 累积。
 * - `openSession`：拉历史事件投影成消息，续聊用同一 session_id 作 threadId。
 * - 错误统一兜底为拟人文案（R-M3.6），不暴露技术细节。
 */

import { create } from "zustand";
import { runAgentStream, sessionsApi } from "@/services";
import type { ChatMessage, TextBlock } from "@/types/chat";
import { applyAguiEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";

/** 流式失败（网络/HTTP）时的拟人兜底；RUN_ERROR 用 bridge 下发的文案。 */
const FRIENDLY_FALLBACK = "我这边好像走神了一下，待会儿再聊好吗？";

/** 本窗口同一时刻只允许一条进行中的流。 */
let activeController: AbortController | null = null;

interface ConversationState {
  currentSessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  /** 开一个新会话（清空当前视图；session 在首次 send 时由 bridge 自动创建）。 */
  newSession: () => void;
  /** 切换到历史会话并加载其上下文。 */
  openSession: (sessionId: string) => Promise<void>;
  /** 发送一条消息并消费流式回复。 */
  send: (text: string) => Promise<void>;
  /** 打断当前流（保留已生成的部分）。 */
  stop: () => void;
}

function newId(): string {
  return crypto.randomUUID();
}

function userMessage(text: string): ChatMessage {
  const id = newId();
  const block: TextBlock = { kind: "text", mid: id, text };
  return { id, role: "user", blocks: [block], status: "complete" };
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  currentSessionId: null,
  messages: [],
  streaming: false,

  newSession() {
    if (get().streaming) get().stop();
    set({ currentSessionId: null, messages: [] });
  },

  async openSession(sessionId) {
    if (get().streaming) get().stop();
    set({ currentSessionId: sessionId, messages: [] });
    try {
      const detail = await sessionsApi.get(sessionId);
      set({ messages: projectSessionEvents(detail.events) });
    } catch {
      set({
        messages: [
          {
            id: newId(),
            role: "assistant",
            blocks: [],
            status: "error",
            error: "这段对话我一时没翻出来，待会儿再试试？",
          },
        ],
      });
    }
  },

  async send(text) {
    const trimmed = text.trim();
    if (!trimmed || get().streaming) return;

    const sessionId = get().currentSessionId ?? newId();
    const assistantId = newId();
    const placeholder: ChatMessage = {
      id: assistantId,
      role: "assistant",
      blocks: [],
      status: "streaming",
    };
    set((s) => ({
      currentSessionId: sessionId,
      streaming: true,
      messages: [...s.messages, userMessage(trimmed), placeholder],
    }));

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
    }
  },

  stop() {
    activeController?.abort();
  },
}));
