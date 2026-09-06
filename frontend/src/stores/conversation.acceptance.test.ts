import { beforeEach, describe, expect, it, vi } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import type { RunSnapshot } from "@/types/run";

vi.mock("@/utils/tauri", () => ({ isTauri: () => false }));
vi.mock("@/lib/persistence/chatUi", () => ({ setLastChatSessionId: vi.fn(async () => undefined) }));
vi.mock("@/services", () => ({
  StreamAdmissionError: class extends Error {},
  runAgentStream: vi.fn(), continueToolResultStream: vi.fn(), editResendLatestStream: vi.fn(),
  modelsApi: { validate: vi.fn() },
  runsApi: { get: vi.fn(), list: vi.fn(async () => ({ runs: [] })), cancel: vi.fn() },
  sessionsApi: {
    get: vi.fn(), getApprovalMode: vi.fn(), list: vi.fn(async () => []),
  },
}));

import { runAgentStream, runsApi } from "@/services";
import { resetConversationCoordinatorsForTests, useConversationStore } from "./conversation";
import { HOME_DRAFT_KEY, useDraftStore } from "./drafts";
import { emptySnapshotFence } from "./snapshotFence";
import { isSessionBusy, sessionRunPresentation } from "./runPresentation";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}
const event = (fields: Record<string, unknown>) => fields as unknown as BaseEvent;

beforeEach(() => {
  vi.clearAllMocks();
  resetConversationCoordinatorsForTests();
  useDraftStore.setState({ workspaces: {}, submissions: {} });
  useConversationStore.setState({
    currentSessionId: null, currentModel: "test", currentReasoningPreset: "none",
    approvalMode: "per_request", approvalModeVersion: 0, approvalModeChanging: false,
    messages: [], sessionViews: {}, historyLoading: false, historyLoadSeq: 0, syncRefreshSeq: 0,
    modelSwitching: false, modelSwitchTarget: null, editingMessage: null,
    runtimeInstanceId: null, runSnapshots: {}, runFence: emptySnapshotFence(),
    stopRequestsByRun: {}, runCalibrationPhase: "ready", unreadRunOutcomeBySession: {},
  });
  vi.mocked(runsApi.get).mockReset().mockImplementation(async (id) => useConversationStore.getState().runSnapshots[id]);
});

function submitDraft(text = "original draft") {
  useDraftStore.getState().setText(HOME_DRAFT_KEY, text);
  const result = useConversationStore.getState().send(text);
  if (!result.accepted) throw new Error("Expected local admission");
  useDraftStore.getState().claimHome(result.sessionId);
  useDraftStore.getState().beginSubmission(result.sessionId, result.sessionId, result.runId);
  return result;
}

function snapshot(runId: string, accepted: boolean | null, status: RunSnapshot["status"], version = 2): RunSnapshot {
  return {
    ...useConversationStore.getState().runSnapshots[runId],
    runtime_instance_id: "runtime-a", input_accepted: accepted, status, version,
    outcome: status === "terminal" ? "cancelled" : null,
    finished_at: status === "terminal" ? "2026-09-06T00:00:00Z" : null,
  };
}

describe("durable input acceptance", () => {
  it("restores an unaccepted terminal into its own draft without overwriting newer input", async () => {
    const gate = deferred();
    vi.mocked(runAgentStream).mockImplementation(async function* (input) {
      yield event({ type: EventType.RUN_STARTED, threadId: input.threadId, runId: input.runId,
        runtimeInstanceId: "runtime-a", inputAccepted: false });
      await gate.promise;
    });
    const send = submitDraft();
    useDraftStore.getState().setText(send.sessionId, "new draft");
    useConversationStore.getState().applyRunSnapshot(snapshot(send.runId, false, "running", 1));
    expect(sessionRunPresentation(useConversationStore.getState(), send.sessionId).phase).toBe("preparing");
    useConversationStore.getState().applyRunSnapshot(snapshot(send.runId, false, "terminal"));
    expect(useDraftStore.getState().workspaces[send.sessionId].text).toBe("original draft\nnew draft");
    expect(useDraftStore.getState().submissions[send.runId]).toBeUndefined();
    expect(useConversationStore.getState().messages.some((message) => message.role === "user")).toBe(false);
    expect(isSessionBusy(useConversationStore.getState(), send.sessionId)).toBe(false);
    gate.resolve(); await send.completion;
    expect(useDraftStore.getState().workspaces[send.sessionId].text).toBe("original draft\nnew draft");
  });

  it("durable acceptance settles the submission once and a later cancel never restores it", async () => {
    const gate = deferred();
    vi.mocked(runAgentStream).mockImplementation(async function* () {
      yield event({ type: EventType.RUN_STARTED }); await gate.promise;
    });
    const send = submitDraft();
    useConversationStore.getState().applyRunSnapshot(snapshot(send.runId, true, "running"));
    expect(useDraftStore.getState().submissions[send.runId]).toBeUndefined();
    useConversationStore.getState().applyRunSnapshot(snapshot(send.runId, true, "terminal", 3));
    expect(useDraftStore.getState().workspaces[send.sessionId]).toBeUndefined();
    expect(useConversationStore.getState().messages.filter((message) => message.role === "user")).toHaveLength(1);
    gate.resolve(); await send.completion;
  });

  it("legacy null acceptance is not interpreted as rejected input", async () => {
    const gate = deferred();
    vi.mocked(runAgentStream).mockImplementation(async function* () {
      yield event({ type: EventType.RUN_STARTED }); await gate.promise;
    });
    const send = submitDraft();
    useConversationStore.getState().applyRunSnapshot(snapshot(send.runId, null, "terminal"));
    expect(useDraftStore.getState().workspaces[send.sessionId]).toBeUndefined();
    expect(useConversationStore.getState().messages.filter((message) => message.role === "user")).toHaveLength(1);
    gate.resolve(); await send.completion;
  });

  it("authoritative accepted input survives a rejected run outcome", async () => {
    const gate = deferred();
    vi.mocked(runAgentStream).mockImplementation(async function* () {
      yield event({ type: EventType.RUN_STARTED }); await gate.promise;
    });
    const send = submitDraft();
    useConversationStore.getState().applyRunSnapshot({
      ...snapshot(send.runId, true, "terminal"), outcome: "rejected",
    });
    gate.resolve();
    await expect(send.completion).resolves.toBe("failed");
    expect(useDraftStore.getState().workspaces[send.sessionId]).toBeUndefined();
    expect(useConversationStore.getState().messages.filter((message) => message.role === "user")).toHaveLength(1);
  });

  it("transport completion cannot discard a submission while registration remains unknown", async () => {
    vi.mocked(runAgentStream).mockImplementation(() => { throw new Error("offline"); });
    vi.mocked(runsApi.get).mockRejectedValueOnce({ code: "network" });
    const send = submitDraft();
    await send.completion;
    expect(useDraftStore.getState().submissions[send.runId]?.text).toBe("original draft");
    await useConversationStore.getState().calibrateRuns();
    expect(useConversationStore.getState().runSnapshots[send.runId]).toBeDefined();
    useConversationStore.getState().applyRunSnapshot(snapshot(send.runId, false, "terminal"));
    expect(useDraftStore.getState().workspaces[send.sessionId]?.text).toBe("original draft");
  });

  it("an identified RUN_ERROR can restore pending input even when the terminal pull fails", async () => {
    vi.mocked(runAgentStream).mockImplementation(async function* (input) {
      yield event({ type: EventType.RUN_STARTED, threadId: input.threadId, runId: input.runId,
        runtimeInstanceId: "runtime-a", inputAccepted: false });
      yield event({ type: EventType.RUN_ERROR, threadId: input.threadId, runId: input.runId,
        runtimeInstanceId: "runtime-a", inputAccepted: false, code: "attachment_invalid", message: "附件需要重新选择。" });
    });
    vi.mocked(runsApi.get).mockRejectedValueOnce({ code: "network" });
    const send = submitDraft();
    await send.completion;
    expect(useDraftStore.getState().workspaces[send.sessionId]?.text).toBe("original draft");
    expect(useConversationStore.getState().messages.filter((message) => message.role === "user")).toHaveLength(0);
    expect(useConversationStore.getState().messages.at(-1)?.error).toBe("附件需要重新选择。");
  });

  it("a snapshot for another session or runtime cannot settle this submission", async () => {
    const gate = deferred();
    vi.mocked(runAgentStream).mockImplementation(async function* () {
      yield event({ type: EventType.RUN_STARTED }); await gate.promise;
    });
    useConversationStore.getState().observeRuntimeIdentity("runtime-a");
    const send = submitDraft();
    const terminal = snapshot(send.runId, false, "terminal");
    useConversationStore.getState().applyRunSnapshot({ ...terminal, session_id: "other" });
    useConversationStore.getState().applyRunSnapshot({ ...terminal, runtime_instance_id: "runtime-b" });
    expect(useDraftStore.getState().submissions[send.runId]).toBeDefined();
    expect(useConversationStore.getState().runSnapshots[send.runId].status).toBe("queued");
    useConversationStore.getState().applyRunSnapshot(terminal);
    gate.resolve(); await send.completion;
  });
});
