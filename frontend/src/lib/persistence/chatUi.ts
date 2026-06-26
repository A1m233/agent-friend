import { invoke } from "@tauri-apps/api/core";
import type { SessionSummary } from "@/types/meta";
import { isTauri } from "@/utils/tauri";

export interface ChatUiPersistence {
  lastChatSessionId: string | null;
}

export const DEFAULT_CHAT_UI_PERSISTENCE: ChatUiPersistence = {
  lastChatSessionId: null,
};

function normalizeSessionId(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function normalizeChatUiPersistence(value: unknown): ChatUiPersistence {
  if (!value || typeof value !== "object") return DEFAULT_CHAT_UI_PERSISTENCE;
  return {
    lastChatSessionId: normalizeSessionId(
      (value as Partial<ChatUiPersistence>).lastChatSessionId,
    ),
  };
}

export function resolveRestorableChatSessionId(
  lastSessionId: string | null,
  sessions: Pick<SessionSummary, "session_id">[],
): string | null {
  if (!lastSessionId) return null;
  return sessions.some((s) => s.session_id === lastSessionId) ? lastSessionId : null;
}

export async function getChatUiPersistence(): Promise<ChatUiPersistence> {
  if (!isTauri()) return DEFAULT_CHAT_UI_PERSISTENCE;
  return normalizeChatUiPersistence(await invoke("get_chat_ui_persistence"));
}

export async function setLastChatSessionId(sessionId: string | null): Promise<void> {
  if (!isTauri()) return;
  await invoke("set_last_chat_session_id", {
    payload: { sessionId: normalizeSessionId(sessionId) },
  });
}
