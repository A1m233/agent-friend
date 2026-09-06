import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
vi.mock("@/utils/tauri", () => ({ isTauri: () => false }));
vi.mock("@/services", () => ({
  imApi: { listProviders: vi.fn(async () => []) },
  sessionsApi: {
    list: vi.fn(),
    get: vi.fn(),
    getApprovalMode: vi.fn(),
  },
  runAgentStream: vi.fn(),
  editResendLatestStream: vi.fn(),
  continueToolResultStream: vi.fn(),
  runsApi: {
    get: vi.fn(),
    list: vi.fn(async () => ({ runs: [] })),
    cancel: vi.fn(),
    events: vi.fn(),
    runtimeSnapshot: vi.fn(),
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
  reattachSessionRuntimeForSync,
  resetSessionSyncForTests,
  startSessionSyncSubscriber,
} from "./sessionSync";
import { useSessionsStore } from "./sessions";
import { useCommandRuntimeStore } from "./commandRuntime";
import { useFilesystemApprovalsStore } from "./filesystemApprovals";
import { emptySnapshotFence } from "./snapshotFence";
import { isSessionBusy, sessionActiveRun } from "./runPresentation";
import type { PushEnvelope } from "@/types/push";

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

function changed(sessionId: string, revision: number, fields: Partial<PushEnvelope> = {}) {
  return {
    kind: "session_changed" as const,
    session_id: sessionId,
    seq: revision,
    source_kind: null,
    events: [],
    revision,
    ...fields,
  };
}

describe("sessionSync", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    resetSessionSyncForTests();
    useConversationStore.getState().invalidateBackgroundRefreshes();
    useSessionsStore.setState({ list: [], loading: false, loaded: true, error: false });
    useConversationStore.setState({
      currentSessionId: "s1",
      approvalMode: "per_request",
      approvalModeVersion: 0,
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
    vi.mocked(sessionsApi.getApprovalMode).mockImplementation(async (sessionId) => ({
      session_id: sessionId, approval_mode: "per_request", approval_mode_version: 0,
    }));
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
    expect(sessionsApi.getApprovalMode).not.toHaveBeenCalled();
  });

  it("does not query approval mode for content or unrelated metadata bursts", async () => {
    for (let revision = 1; revision <= 30; revision += 1) {
      handleSessionChangedForSync(changed("s1", revision, {
        change_kind: revision % 2 ? "content" : "metadata", origin: "ag_ui",
      }));
    }
    await vi.runAllTimersAsync();
    expect(sessionsApi.getApprovalMode).not.toHaveBeenCalled();
    expect(sessionsApi.get).toHaveBeenCalledTimes(1);
  });

  it("refreshes an active session for an older approval event after newer content", async () => {
    const running = activeRun();
    useConversationStore.setState({ runSnapshots: { [running.run_id]: running } });
    vi.mocked(sessionsApi.getApprovalMode).mockResolvedValueOnce({
      session_id: "s1", approval_mode: "session_auto", approval_mode_version: 4,
    });
    handleSessionChangedForSync(changed("s1", 12, { change_kind: "content" }));
    const approval = changed("s1", 11, { change_kind: "metadata", origin: "approval_mode" });
    handleSessionChangedForSync(approval);
    handleSessionChangedForSync(approval);
    expect(sessionsApi.getApprovalMode).toHaveBeenCalledExactlyOnceWith("s1");
    await vi.runAllTimersAsync();
    expect(useConversationStore.getState()).toMatchObject({
      approvalMode: "session_auto", approvalModeVersion: 4,
    });
    expect(isSessionBusy(useConversationStore.getState(), "s1")).toBe(true);
    expect(sessionsApi.get).not.toHaveBeenCalled();
  });

  it("does not query another session's approval mode", async () => {
    handleSessionChangedForSync(changed("s2", 1, {
      change_kind: "metadata", origin: "approval_mode",
    }));
    await vi.runAllTimersAsync();
    expect(sessionsApi.getApprovalMode).not.toHaveBeenCalled();
  });

  it("starts a mode calibration immediately for an existing session", async () => {
    const stop = startSessionSyncSubscriber();
    expect(sessionsApi.getApprovalMode).toHaveBeenCalledExactlyOnceWith("s1");
    await Promise.resolve();
    stop();
  });

  it("does not treat selecting an optimistic new session as a mode-read trigger", async () => {
    useConversationStore.setState({ currentSessionId: null });
    const stop = startSessionSyncSubscriber();
    useConversationStore.setState({ currentSessionId: "not-created-yet" });
    await Promise.resolve();
    expect(sessionsApi.getApprovalMode).not.toHaveBeenCalled();
    stop();
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
    expect(sessionsApi.getApprovalMode).toHaveBeenCalledExactlyOnceWith("s1");
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

  it("reattaches an active run from its runtime journal and deduplicates live overlap", async () => {
    const running = { ...activeRun("run-resident"), runtime_instance_id: "bridge-1" };
    vi.mocked(runsApi.runtimeSnapshot).mockResolvedValueOnce({
      runtime: {
        bridgeInstanceId: "bridge-1",
        supervisorGeneration: 4,
        startedAt: "2026-08-18T00:00:00Z",
        startReason: "desktop_start",
      },
      sessionRuntime: {
        resident: true,
        modelHistoryGeneration: 0,
        providerRequestCount: 1,
        cacheEpochGeneration: 0,
        cacheEpochReason: null,
      },
      runs: [running],
      approvals: [],
      commandProcesses: [],
    });
    vi.mocked(runsApi.events).mockResolvedValueOnce({
      runtime_instance_id: "bridge-1",
      run_id: running.run_id,
      session_id: running.session_id,
      first_seq: 1,
      next_seq: 3,
      truncated: false,
      gap_before_requested_seq: false,
      events: [
        { event_seq: 1, event: { type: "text_delta", text: "仍在" } },
        { event_seq: 2, event: { type: "text_delta", text: "运行" } },
      ],
    });
    vi.spyOn(useConversationStore.getState(), "calibrateRuns").mockResolvedValue();
    vi.spyOn(useCommandRuntimeStore.getState(), "refresh").mockResolvedValue();
    vi.spyOn(useFilesystemApprovalsStore.getState(), "refresh").mockResolvedValue();

    await reattachSessionRuntimeForSync("s1");
    await vi.advanceTimersByTimeAsync(33);
    expect(useConversationStore.getState().messages.at(-1)).toMatchObject({
      id: "proactive:run-resident",
      status: "streaming",
      blocks: [{ kind: "text", text: "仍在运行" }],
    });

    handleRunEventForSync({
      kind: "run_event",
      session_id: "s1",
      seq: 10,
      source_kind: null,
      presentation: "conversation",
      run_id: running.run_id,
      runtime_instance_id: "bridge-1",
      run_event_seq: 2,
      events: [{ type: "text_delta", text: "重复" }],
    });
    handleRunEventForSync({
      kind: "run_event",
      session_id: "s1",
      seq: 11,
      source_kind: null,
      presentation: "conversation",
      run_id: running.run_id,
      runtime_instance_id: "bridge-1",
      run_event_seq: 3,
      events: [{ type: "text_delta", text: "中" }],
    });
    await vi.advanceTimersByTimeAsync(33);
    expect(useConversationStore.getState().messages.at(-1)?.blocks).toEqual([
      expect.objectContaining({ kind: "text", text: "仍在运行中" }),
    ]);
  });
});
