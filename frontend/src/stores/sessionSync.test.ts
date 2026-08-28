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
  continueToolResultStream: vi.fn(),
  runsApi: {
    get: vi.fn(),
    list: vi.fn(async () => ({ runs: [] })),
    cancel: vi.fn(),
  },
}));
vi.mock("@/lib/persistence/chatUi", () => ({ setLastChatSessionId: vi.fn() }));

import { runsApi, sessionsApi } from "@/services";
import { useConversationStore } from "./conversation";
import {
  handlePushReconnectedForSync,
  handleRunEventForSync,
  handleRunChangedForSync,
  handleSessionChangedForSync,
  resetSessionSyncForTests,
  startSessionSyncSubscriber,
} from "./sessionSync";
import { useSessionsStore } from "./sessions";
import { emptySnapshotFence } from "./snapshotFence";
import { isSessionBusy, sessionActiveRun } from "./runPresentation";

function activeRun(runId = "run-active", sessionId = "s1") {
  return {
    run_id: runId,
    session_id: sessionId,
    status: "running" as const,
    outcome: null,
    reason_code: "",
    channel: "text" as const,
    origin: "ag_ui",
    control_scope: "desktop_text",
    created_at: "2026-08-18T00:00:00Z",
    started_at: "2026-08-18T00:00:01Z",
    finished_at: null,
    version: 1,
  };
}

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
      historyLoading: false,
      historyLoadSeq: 0,
      syncRefreshSeq: 0,
      sessionViews: {},
      runSnapshots: {},
      runCalibrationPhase: "ready",
      runFence: emptySnapshotFence(),
      unreadRunOutcomeBySession: {},
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
    const running = activeRun();
    useConversationStore.setState({ runSnapshots: { [running.run_id]: running } });
    vi.mocked(runsApi.list).mockResolvedValueOnce({ runs: [running] });
    const stop = startSessionSyncSubscriber();
    handleSessionChangedForSync(changed("s1", 1));
    await vi.runAllTimersAsync();
    expect(sessionsApi.get).not.toHaveBeenCalled();
    useConversationStore.getState().applyRunSnapshot({
      ...running,
      status: "terminal",
      outcome: "completed",
      finished_at: "2026-08-18T00:00:02Z",
      version: 2,
    });
    await vi.runAllTimersAsync();
    expect(sessionsApi.get).toHaveBeenCalled();
    stop();
  });

  it("reconnect calibration refreshes list and current detail", async () => {
    handlePushReconnectedForSync();
    await vi.runAllTimersAsync();
    expect(sessionsApi.list).toHaveBeenCalledTimes(1);
    expect(sessionsApi.get).toHaveBeenCalledTimes(1);
  });

  it("calibrates active runs immediately after the subscriber starts", async () => {
    vi.mocked(runsApi.list).mockResolvedValueOnce({
      runs: [{
        run_id: "run-awaiting-approval",
        session_id: "s1",
        status: "running",
        outcome: null,
        reason_code: "",
        channel: "text",
        origin: "ag_ui",
        control_scope: "desktop_text",
        created_at: "2026-08-18T00:00:00Z",
        started_at: "2026-08-18T00:00:01Z",
        finished_at: null,
        version: 1,
      }],
    });

    const stop = startSessionSyncSubscriber();
    await vi.waitFor(() => {
      expect(sessionActiveRun(useConversationStore.getState(), "s1")?.run_id)
        .toBe("run-awaiting-approval");
    });
    stop();
  });

  it("run_changed updates only the matching session run projection", () => {
    handleRunChangedForSync({
      kind: "run_changed",
      session_id: "background",
      seq: 4,
      source_kind: null,
      events: [],
      run: {
        run_id: "run-b",
        session_id: "background",
        status: "running",
        outcome: null,
        reason_code: "",
        channel: "text",
        origin: "ag_ui",
        control_scope: "desktop_text",
        created_at: "2026-07-25T00:00:00Z",
        started_at: "2026-07-25T00:00:01Z",
        finished_at: null,
        version: 2,
      },
    });
    const state = useConversationStore.getState();
    expect(sessionActiveRun(state, "background")?.run_id).toBe("run-b");
    expect(isSessionBusy(state, "s1")).toBe(false);
  });

  it("coalesces conversation run_event deltas and flushes before terminal refresh", async () => {
    handleRunChangedForSync({
      kind: "run_changed",
      session_id: "s1",
      seq: 1,
      source_kind: null,
      events: [],
      run: {
        run_id: "run-social",
        session_id: "s1",
        status: "running",
        outcome: null,
        reason_code: "",
        channel: "text",
        origin: "system",
        control_scope: "desktop_text",
        created_at: "2026-08-02T00:00:00Z",
        started_at: "2026-08-02T00:00:01Z",
        finished_at: null,
        version: 1,
      },
    });
    for (const [seq, text] of [[2, "正在"], [3, "继续"]] as const) {
      handleRunEventForSync({
        kind: "run_event",
        session_id: "s1",
        seq,
        source_kind: "social_auth_completed",
        presentation: "conversation",
        run_id: "run-social",
        events: [{ type: "text_delta", text }],
      });
    }
    expect(useConversationStore.getState().messages).toEqual([]);
    await vi.advanceTimersByTimeAsync(33);
    expect(useConversationStore.getState().messages.at(-1)).toMatchObject({
      id: "proactive:run-social",
      status: "streaming",
      blocks: [{ kind: "text", text: "正在继续" }],
    });

    handleRunEventForSync({
      kind: "run_event",
      session_id: "s1",
      seq: 4,
      source_kind: "social_auth_completed",
      presentation: "conversation",
      run_id: "run-social",
      events: [{ type: "done" }],
    });
    handleRunChangedForSync({
      kind: "run_changed",
      session_id: "s1",
      seq: 5,
      source_kind: null,
      events: [],
      run: {
        run_id: "run-social",
        session_id: "s1",
        status: "terminal",
        outcome: "completed",
        reason_code: "",
        channel: "text",
        origin: "system",
        control_scope: "desktop_text",
        created_at: "2026-08-02T00:00:00Z",
        started_at: "2026-08-02T00:00:01Z",
        finished_at: "2026-08-02T00:00:02Z",
        version: 2,
      },
    });
    expect(useConversationStore.getState().messages.at(-1)?.status).toBe("complete");
  });
});
