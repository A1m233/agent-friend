import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => false }));
vi.mock("@/services", () => ({
  sessionsApi: {
    list: vi.fn(),
    get: vi.fn(),
  },
  runAgentStream: vi.fn(),
  editResendLatestStream: vi.fn(),
}));
vi.mock("@/lib/persistence/chatUi", () => ({ setLastChatSessionId: vi.fn() }));

import { sessionsApi } from "@/services";
import { useConversationStore } from "./conversation";
import {
  handlePushReconnectedForSync,
  handleSessionChangedForSync,
  resetSessionSyncForTests,
  startSessionSyncSubscriber,
} from "./sessionSync";
import { useSessionsStore } from "./sessions";

function changed(sessionId: string, revision: number) {
  return {
    kind: "session_changed" as const,
    session_id: sessionId,
    seq: revision,
    source_kind: null,
    events: [],
    revision,
  };
}

describe("sessionSync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    resetSessionSyncForTests();
    useSessionsStore.setState({ list: [], loading: false, loaded: true });
    useConversationStore.setState({
      currentSessionId: "s1",
      messages: [],
      streaming: false,
      historyLoading: false,
      historyLoadSeq: 0,
      syncRefreshSeq: 0,
    });
    vi.mocked(sessionsApi.list).mockResolvedValue([]);
    vi.mocked(sessionsApi.get).mockResolvedValue({
      session_id: "s1",
      title: "t",
      persona: "default",
      model: "fake",
      events: [],
    });
  });

  it("coalesces duplicate revisions and refreshes current detail", async () => {
    handleSessionChangedForSync(changed("s1", 1));
    handleSessionChangedForSync(changed("s1", 1));
    handleSessionChangedForSync(changed("s1", 2));
    await vi.runAllTimersAsync();
    expect(sessionsApi.list).toHaveBeenCalledTimes(1);
    expect(sessionsApi.get).toHaveBeenCalledTimes(1);
  });

  it("refreshes only the list for a non-current session", async () => {
    handleSessionChangedForSync(changed("s2", 1));
    await vi.runAllTimersAsync();
    expect(sessionsApi.list).toHaveBeenCalledTimes(1);
    expect(sessionsApi.get).not.toHaveBeenCalled();
  });

  it("defers current detail while streaming and flushes after settle", async () => {
    useConversationStore.setState({ streaming: true });
    const stop = startSessionSyncSubscriber();
    handleSessionChangedForSync(changed("s1", 1));
    await vi.runAllTimersAsync();
    expect(sessionsApi.get).not.toHaveBeenCalled();
    useConversationStore.setState({ streaming: false });
    await vi.runAllTimersAsync();
    expect(sessionsApi.get).toHaveBeenCalledTimes(1);
    stop();
  });

  it("reconnect calibration refreshes list and current detail", async () => {
    handlePushReconnectedForSync();
    await vi.runAllTimersAsync();
    expect(sessionsApi.list).toHaveBeenCalledTimes(1);
    expect(sessionsApi.get).toHaveBeenCalledTimes(1);
  });
});
