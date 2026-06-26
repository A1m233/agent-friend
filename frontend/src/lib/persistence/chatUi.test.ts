import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

vi.mock("@/utils/tauri", () => ({
  isTauri: vi.fn(() => true),
}));

import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "@/utils/tauri";
import {
  DEFAULT_CHAT_UI_PERSISTENCE,
  getChatUiPersistence,
  normalizeChatUiPersistence,
  resolveRestorableChatSessionId,
  setLastChatSessionId,
} from "./chatUi";

const mockInvoke = vi.mocked(invoke);
const mockIsTauri = vi.mocked(isTauri);

describe("chat UI persistence facade", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockIsTauri.mockReturnValue(true);
  });

  it("normalizes persisted state from Tauri", async () => {
    mockInvoke.mockResolvedValue({ lastChatSessionId: " sid-1 " });

    await expect(getChatUiPersistence()).resolves.toEqual({
      lastChatSessionId: "sid-1",
    });
    expect(mockInvoke).toHaveBeenCalledWith("get_chat_ui_persistence");
  });

  it("writes the normalized session id through invoke", async () => {
    mockInvoke.mockResolvedValue(undefined);

    await setLastChatSessionId(" sid-2 ");

    expect(mockInvoke).toHaveBeenCalledWith("set_last_chat_session_id", {
      payload: { sessionId: "sid-2" },
    });
  });

  it("does not use browser persistence outside Tauri", async () => {
    mockIsTauri.mockReturnValue(false);

    await expect(getChatUiPersistence()).resolves.toBe(DEFAULT_CHAT_UI_PERSISTENCE);
    await setLastChatSessionId("sid-3");

    expect(mockInvoke).not.toHaveBeenCalled();
  });

  it("normalizes malformed values to the default shape", () => {
    expect(normalizeChatUiPersistence(null)).toBe(DEFAULT_CHAT_UI_PERSISTENCE);
    expect(normalizeChatUiPersistence({ lastChatSessionId: " " })).toEqual({
      lastChatSessionId: null,
    });
  });

  it("only restores a session id that still exists in the refreshed list", () => {
    const sessions = [{ session_id: "sid-a" }, { session_id: "sid-b" }];

    expect(resolveRestorableChatSessionId("sid-b", sessions)).toBe("sid-b");
    expect(resolveRestorableChatSessionId("sid-missing", sessions)).toBeNull();
    expect(resolveRestorableChatSessionId(null, sessions)).toBeNull();
  });
});
