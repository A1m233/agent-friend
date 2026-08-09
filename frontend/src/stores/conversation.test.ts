import { beforeEach, describe, expect, it, vi } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";

// mock services 层：只控制 runAgentStream / sessionsApi.get 的行为，验证 store 的错误兜底。
vi.mock("@/services", () => ({
  StreamAdmissionError: class StreamAdmissionError extends Error {},
  continueToolResultStream: vi.fn(),
  editResendLatestStream: vi.fn(),
  modelsApi: { validate: vi.fn() },
  runAgentStream: vi.fn(),
  runsApi: {
    get: vi.fn(async (runId: string) => ({
      run_id: runId,
      session_id: "",
      status: "terminal",
      outcome: "completed",
      reason_code: "",
      channel: "text",
      origin: "ag_ui",
      control_scope: "desktop_text",
      created_at: "2026-07-25T00:00:00Z",
      started_at: "2026-07-25T00:00:00Z",
      finished_at: "2026-07-25T00:00:01Z",
      version: 3,
    })),
    list: vi.fn(async () => ({ runs: [] })),
    cancel: vi.fn(),
  },
  sessionsApi: { get: vi.fn(), switchModel: vi.fn() },
}));

vi.mock("@/lib/persistence/chatUi", () => ({
  setLastChatSessionId: vi.fn(() => Promise.resolve()),
}));

import { setLastChatSessionId } from "@/lib/persistence/chatUi";
import {
  continueToolResultStream,
  editResendLatestStream,
  modelsApi,
  runAgentStream,
  runsApi,
  sessionsApi,
} from "@/services";
import type { SessionDetail } from "@/types/meta";
import type { CancelRunResponse, RunSnapshot } from "@/types/run";
import type { SessionMessageSearchResult } from "@/types/search";
import { useConversationStore, type SendStartResult } from "./conversation";
import { useDraftStore } from "./drafts";

const mockStream = vi.mocked(runAgentStream);
const mockRunGet = vi.mocked(runsApi.get);
const mockRunList = vi.mocked(runsApi.list);
const mockRunCancel = vi.mocked(runsApi.cancel);
const mockEditStream = vi.mocked(editResendLatestStream);
const mockContinueStream = vi.mocked(continueToolResultStream);
const mockGet = vi.mocked(sessionsApi.get);
const mockSwitchModel = vi.mocked(sessionsApi.switchModel);
const mockValidateModel = vi.mocked(modelsApi.validate);
const mockSetLastChatSessionId = vi.mocked(setLastChatSessionId);

/** 技术细节特征：HTTP 状态码 / 英文异常字样等——拟人文案里**不应**出现任何一项。 */
const TECH_LEAK = /http|error|exception|status|\b[1-5]\d{2}\b|undefined|null|stack/i;

function ev(e: Record<string, unknown>): BaseEvent {
  return e as unknown as BaseEvent;
}

function lastMessage() {
  const { messages } = useConversationStore.getState();
  return messages[messages.length - 1];
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function finishAcceptedSend(result: SendStartResult) {
  expect(result.accepted).toBe(true);
  if (!result.accepted) throw new Error(`send rejected: ${result.reason}`);
  return result.completion;
}

function sessionDetail(sessionId: string, content: string, model = "test"): SessionDetail {
  return {
    session_id: sessionId,
    title: sessionId,
    persona: "default",
    model,
    events: [
      {
        type: "assistant_message",
        uuid: `${sessionId}:assistant`,
        ts: "2026-06-10T00:00:00Z",
        payload: { content, partial: false },
      },
    ],
  };
}

function runSnapshot(
  runId: string,
  sessionId: string,
  status: RunSnapshot["status"],
  version: number,
  outcome: RunSnapshot["outcome"] = null,
): RunSnapshot {
  return {
    run_id: runId,
    session_id: sessionId,
    status,
    outcome,
    reason_code: "",
    channel: "text",
    origin: "ag_ui",
    control_scope: "desktop_text",
    created_at: "2026-07-25T00:00:00Z",
    started_at: "2026-07-25T00:00:00Z",
    finished_at: status === "terminal" ? "2026-07-25T00:00:01Z" : null,
    version,
  };
}

function firstText() {
  const block = useConversationStore.getState().messages[0]?.blocks[0];
  return block?.kind === "text" ? block.text : "";
}

function messageTexts() {
  return useConversationStore
    .getState()
    .messages.map((message) =>
      message.blocks
        .filter((block) => block.kind === "text")
        .map((block) => (block.kind === "text" ? block.text : ""))
        .join("\n\n"),
    );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockReset();
  mockRunCancel.mockReset();
  useConversationStore.getState().newSession();
  useConversationStore.setState({
    currentSessionId: null,
    currentModel: null,
    messages: [],
    streaming: false,
    historyLoading: false,
    modelSwitching: false,
    modelSwitchTarget: null,
    editingMessageId: null,
    sessionViews: {},
    runSnapshots: {},
    viewAccessedAt: {},
    activeRunIdBySession: {},
    stopRequestsByRun: {},
    unreadRunOutcomeBySession: {},
    detachedSessionId: null,
    searchTarget: null,
  });
  useDraftStore.setState({ workspaces: {}, submissions: {} });
  mockSetLastChatSessionId.mockClear();
  mockValidateModel.mockImplementation(async (model) => ({ model, available: true }));
  mockSwitchModel.mockImplementation(async (sessionId, model) => ({
    session_id: sessionId,
    model,
  }));
});

describe("conversation store · cancel lifecycle", () => {
  it("立即进入取消中、去重请求，并在 Runtime 终态前保持 busy", async () => {
    let streamSignal: AbortSignal | undefined;
    mockStream.mockImplementation((_input, opts) => (async function* () {
      const signal = opts?.signal;
      streamSignal = signal;
      await new Promise<void>((_resolve, reject) => {
        if (signal?.aborted) {
          reject(new Error("aborted"));
          return;
        }
        signal?.addEventListener("abort", () => reject(new Error("aborted")), {
          once: true,
        });
      });
      yield ev({ type: EventType.RUN_FINISHED, threadId: "unused", runId: "unused" });
    })());

    const cancelGate = deferred<CancelRunResponse>();
    const terminalGate = deferred<RunSnapshot>();
    mockRunCancel.mockReturnValueOnce(cancelGate.promise);
    mockRunGet.mockReturnValueOnce(terminalGate.promise);

    const sendResult = useConversationStore.getState().send("请停下来");
    expect(sendResult.accepted).toBe(true);
    if (!sendResult.accepted) return;
    expect(streamSignal?.aborted).toBe(false);

    const firstStop = useConversationStore.getState().stop();
    expect(useConversationStore.getState().stopRequestsByRun[sendResult.runId]).toBe(
      "requesting",
    );
    await expect(useConversationStore.getState().stop()).resolves.toBe("already-pending");
    expect(mockRunCancel).toHaveBeenCalledTimes(1);

    cancelGate.resolve({
      result: "accepted",
      run: runSnapshot(sendResult.runId, sendResult.sessionId, "running", 2),
    });
    await expect(firstStop).resolves.toBe("accepted");

    expect(streamSignal?.aborted).toBe(true);
    expect(useConversationStore.getState().stopRequestsByRun[sendResult.runId]).toBe(
      "accepted",
    );
    expect(useConversationStore.getState().activeRunIdBySession[sendResult.sessionId]).toBe(
      sendResult.runId,
    );
    expect(useConversationStore.getState().streaming).toBe(true);
    expect(useConversationStore.getState().send("不能抢跑下一条")).toEqual({
      accepted: false,
      reason: "busy",
    });

    terminalGate.resolve(
      runSnapshot(sendResult.runId, sendResult.sessionId, "terminal", 3, "cancelled"),
    );
    await expect(sendResult.completion).resolves.toBe("interrupted");
    expect(useConversationStore.getState().stopRequestsByRun[sendResult.runId]).toBeUndefined();
    expect(useConversationStore.getState().activeRunIdBySession[sendResult.sessionId]).toBeUndefined();
    expect(useConversationStore.getState().streaming).toBe(false);
  });

  it("取消 API 失败时撤销取消中状态并触发权威状态校准", async () => {
    useConversationStore.setState({
      currentSessionId: "sid-cancel-failed",
      streaming: true,
      activeRunIdBySession: { "sid-cancel-failed": "run-cancel-failed" },
    });
    mockRunCancel.mockRejectedValueOnce(new Error("bridge offline"));

    await expect(useConversationStore.getState().stop()).resolves.toBe("failed");

    expect(useConversationStore.getState().stopRequestsByRun).toEqual({});
    expect(mockRunList).toHaveBeenCalledWith(true);
  });

  it("终态 push 先到时忽略 SSE 收尾拉到的旧 running 快照", async () => {
    mockStream.mockImplementation((_input, opts) => (async function* () {
      await new Promise<void>((_resolve, reject) => {
        opts?.signal?.addEventListener("abort", () => reject(new Error("aborted")), {
          once: true,
        });
      });
      yield ev({ type: EventType.RUN_FINISHED, threadId: "unused", runId: "unused" });
    })());
    const stalePull = deferred<RunSnapshot>();
    mockRunGet.mockReturnValueOnce(stalePull.promise);

    const sendResult = useConversationStore.getState().send("测试终态竞态");
    expect(sendResult.accepted).toBe(true);
    if (!sendResult.accepted) return;
    mockRunCancel.mockResolvedValueOnce({
      result: "accepted",
      run: runSnapshot(sendResult.runId, sendResult.sessionId, "running", 2),
    });

    await expect(useConversationStore.getState().stop()).resolves.toBe("accepted");
    useConversationStore.getState().applyRunSnapshot(
      runSnapshot(sendResult.runId, sendResult.sessionId, "terminal", 4, "cancelled"),
    );
    stalePull.resolve(runSnapshot(sendResult.runId, sendResult.sessionId, "running", 3));

    await expect(sendResult.completion).resolves.toBe("interrupted");
    expect(useConversationStore.getState().runSnapshots[sendResult.runId].version).toBe(4);
    expect(useConversationStore.getState().activeRunIdBySession[sendResult.sessionId]).toBeUndefined();
    expect(useConversationStore.getState().streaming).toBe(false);
  });

  it("终态先于取消响应到达时不会被旧响应重新写回取消中", async () => {
    const cancelGate = deferred<CancelRunResponse>();
    mockRunCancel.mockReturnValueOnce(cancelGate.promise);
    useConversationStore.setState({
      currentSessionId: "sid-race",
      streaming: true,
      activeRunIdBySession: { "sid-race": "run-race" },
    });

    const stopPromise = useConversationStore.getState().stop();
    useConversationStore.getState().applyRunSnapshot(
      runSnapshot("run-race", "sid-race", "terminal", 3, "cancelled"),
    );
    cancelGate.resolve({
      result: "accepted",
      run: runSnapshot("run-race", "sid-race", "running", 2),
    });

    await expect(stopPromise).resolves.toBe("accepted");
    expect(useConversationStore.getState().runSnapshots["run-race"].status).toBe("terminal");
    expect(useConversationStore.getState().activeRunIdBySession["sid-race"]).toBeUndefined();
    expect(useConversationStore.getState().stopRequestsByRun["run-race"]).toBeUndefined();
    expect(useConversationStore.getState().streaming).toBe(false);
  });
});

describe("conversation store · 错误状态兜底 (AC-M3.4)", () => {
  it("流式失败（带 http 状态的技术错误）→ 用户看到拟人兜底，不漏技术细节", async () => {
    // 模拟 stream.ts 在 HTTP 非 2xx 时抛的技术错误（store 的 for-await 求值即抛、被 catch）。
    mockStream.mockImplementation(() => {
      throw new Error("ag-ui run failed: http 503");
    });

    await finishAcceptedSend(useConversationStore.getState().send("在吗"));

    const m = lastMessage();
    expect(m.role).toBe("assistant");
    expect(m.status).toBe("error");
    expect(m.error && m.error.length).toBeGreaterThan(0);
    expect(m.error!).not.toMatch(TECH_LEAK);
    // 兜底后流式态应复位，可继续对话（"可恢复"）。
    expect(useConversationStore.getState().streaming).toBe(false);
  });

  it("RUN_ERROR 事件 → 展示 bridge 清洗后的文案，不漏技术细节", async () => {
    mockStream.mockImplementation(async function* () {
      yield ev({
        type: EventType.RUN_ERROR,
        message: "模型服务这会儿过载了，这轮没有生成完整回答。稍后可以重试。",
        code: "rate_limit",
      });
    });

    const outcome = await finishAcceptedSend(useConversationStore.getState().send("hi"));

    const m = lastMessage();
    expect(m.status).toBe("error");
    expect(m.error!).not.toMatch(TECH_LEAK);
    expect(m.errorCode).toBe("rate_limit");
    expect(outcome).toBe("failed");
  });

  it("历史会话加载失败 → 拟人兜底，不漏技术细节", async () => {
    mockGet.mockRejectedValue({ friendly: "x", code: "network" });

    await useConversationStore.getState().openSession("sid-1");

    const m = lastMessage();
    expect(m.status).toBe("error");
    expect(m.error!).not.toMatch(TECH_LEAK);
  });

  it("自动恢复历史会话失败时回到首页态", async () => {
    mockGet.mockRejectedValue({ friendly: "x", code: "http_error" });

    const ok = await useConversationStore
      .getState()
      .openSession("sid-missing", { failureMode: "home", remember: false });

    expect(ok).toBe(false);
    expect(useConversationStore.getState().currentSessionId).toBeNull();
    expect(useConversationStore.getState().historyLoading).toBe(false);
    expect(useConversationStore.getState().messages).toEqual([]);
    expect(mockSetLastChatSessionId).not.toHaveBeenCalled();
  });

  it("进入和清空会话时同步可恢复 UI 指针", async () => {
    mockGet.mockResolvedValue(sessionDetail("sid-1", "历史消息"));

    await useConversationStore.getState().openSession("sid-1");

    expect(mockSetLastChatSessionId).toHaveBeenLastCalledWith("sid-1");

    useConversationStore.getState().newSession();

    expect(mockSetLastChatSessionId).toHaveBeenLastCalledWith(null);
  });

  it("从归档搜索结果打开消息时保持 detached，并提交稳定事件锚点", async () => {
    mockGet.mockResolvedValue(sessionDetail("sid-archived", "归档历史"));
    const result: SessionMessageSearchResult = {
      result_id: "message:sid-archived:a-anchor",
      kind: "message",
      session_id: "sid-archived",
      session_title: "归档对话",
      archived: true,
      role: "assistant",
      occurred_at: "2026-08-05T00:00:00Z",
      anchor_event_uuid: "a-anchor",
      matched_field: "content",
      segments: [{ text: "关键词", matched: true }],
    };

    const opened = await useConversationStore.getState().openSearchResult(result, "关键词");

    expect(opened).toBe(true);
    expect(useConversationStore.getState().currentSessionId).toBe("sid-archived");
    expect(useConversationStore.getState().detachedSessionId).toBe("sid-archived");
    expect(useConversationStore.getState().searchTarget).toMatchObject({
      sessionId: "sid-archived",
      anchorEventUuid: "a-anchor",
      query: "关键词",
    });
    expect(mockSetLastChatSessionId).not.toHaveBeenCalled();
  });

  it("搜索结果在打开失败时恢复此前会话，不用错误页覆盖它", async () => {
    mockGet.mockResolvedValueOnce(sessionDetail("sid-current", "当前历史"));
    await useConversationStore.getState().openSession("sid-current");
    mockGet.mockRejectedValueOnce(new Error("deleted"));
    const result: SessionMessageSearchResult = {
      result_id: "message:sid-deleted:u-1",
      kind: "message",
      session_id: "sid-deleted",
      session_title: "已删除",
      archived: false,
      role: "user",
      occurred_at: "2026-08-05T00:00:00Z",
      anchor_event_uuid: "u-1",
      matched_field: "content",
      segments: [{ text: "needle", matched: true }],
    };

    const opened = await useConversationStore.getState().openSearchResult(result, "needle");

    expect(opened).toBe(false);
    expect(useConversationStore.getState().currentSessionId).toBe("sid-current");
    expect(firstText()).toBe("当前历史");
    expect(useConversationStore.getState().searchTarget).toBeNull();
  });

  it("历史会话加载期间暴露 loading，完成后复位并渲染历史消息", async () => {
    const beforeSeq = useConversationStore.getState().historyLoadSeq;
    const pending = deferred<SessionDetail>();
    mockGet.mockReturnValueOnce(pending.promise);

    const open = useConversationStore.getState().openSession("sid-1");

    expect(useConversationStore.getState().currentSessionId).toBe("sid-1");
    expect(useConversationStore.getState().historyLoading).toBe(true);
    expect(useConversationStore.getState().messages).toEqual([]);

    pending.resolve(sessionDetail("sid-1", "历史消息"));
    await open;

    expect(useConversationStore.getState().historyLoading).toBe(false);
    expect(useConversationStore.getState().historyLoadSeq).toBe(beforeSeq + 1);
    expect(firstText()).toBe("历史消息");
  });

  it("快速切换历史会话时，旧请求返回不能覆盖当前会话", async () => {
    const beforeSeq = useConversationStore.getState().historyLoadSeq;
    const slow = deferred<SessionDetail>();
    const fast = deferred<SessionDetail>();
    mockGet.mockImplementation((sessionId) =>
      sessionId === "sid-a" ? slow.promise : fast.promise,
    );

    const openA = useConversationStore.getState().openSession("sid-a");
    const openB = useConversationStore.getState().openSession("sid-b");

    fast.resolve(sessionDetail("sid-b", "B 的历史"));
    await openB;

    expect(useConversationStore.getState().currentSessionId).toBe("sid-b");
    expect(useConversationStore.getState().historyLoading).toBe(false);
    expect(useConversationStore.getState().historyLoadSeq).toBe(beforeSeq + 1);
    expect(firstText()).toBe("B 的历史");

    slow.resolve(sessionDetail("sid-a", "A 的历史"));
    await openA;

    expect(useConversationStore.getState().currentSessionId).toBe("sid-b");
    expect(useConversationStore.getState().historyLoading).toBe(false);
    expect(useConversationStore.getState().historyLoadSeq).toBe(beforeSeq + 1);
    expect(firstText()).toBe("B 的历史");
  });

  it("正常流式 → 文本累积并完成（兜底不误触发）", async () => {
    mockStream.mockImplementation(async function* () {
      yield ev({ type: EventType.TEXT_MESSAGE_START, messageId: "m1", role: "assistant" });
      yield ev({ type: EventType.TEXT_MESSAGE_CONTENT, messageId: "m1", delta: "你好" });
      yield ev({ type: EventType.RUN_FINISHED, threadId: "t", runId: "r" });
    });

    const outcome = await finishAcceptedSend(useConversationStore.getState().send("hi"));

    const m = lastMessage();
    expect(m.status).toBe("complete");
    expect(outcome).toBe("completed");
    expect(m.blocks.some((b) => b.kind === "text")).toBe(true);
    expect(mockSetLastChatSessionId).toHaveBeenCalledWith(
      useConversationStore.getState().currentSessionId,
    );
  });

  it("消息接纳不等待流式完成，调用方可立即清空草稿", async () => {
    const streamGate = deferred<void>();
    mockStream.mockImplementation(async function* () {
      await streamGate.promise;
      yield ev({ type: EventType.RUN_FINISHED, threadId: "t", runId: "r" });
    });

    const result = useConversationStore.getState().send("立即发送");

    expect(result.accepted).toBe(true);
    expect(useConversationStore.getState().streaming).toBe(true);
    expect(messageTexts()).toContain("立即发送");
    if (!result.accepted) throw new Error(`send rejected: ${result.reason}`);

    let completed = false;
    void result.completion.then(() => { completed = true; });
    await Promise.resolve();
    expect(completed).toBe(false);

    streamGate.resolve();
    await expect(result.completion).resolves.toBe("completed");
  });

  it("未接纳的发送明确返回原因且不改动消息", async () => {
    expect(useConversationStore.getState().send("   ")).toEqual({
      accepted: false,
      reason: "empty",
    });

    const streamGate = deferred<void>();
    mockStream.mockImplementation(async function* () {
      await streamGate.promise;
      yield ev({ type: EventType.RUN_FINISHED, threadId: "t", runId: "r" });
    });
    const accepted = useConversationStore.getState().send("第一条");
    expect(accepted.accepted).toBe(true);
    expect(useConversationStore.getState().send("第二条")).toEqual({
      accepted: false,
      reason: "busy",
    });
    expect(messageTexts()).not.toContain("第二条");

    streamGate.resolve();
    if (accepted.accepted) await accepted.completion;
  });

  it("切到新会话后可并发发送，两个 run 只更新各自 session workspace", async () => {
    const gates = new Map<string, ReturnType<typeof deferred<void>>>();
    mockStream.mockImplementation((input) => (async function* () {
      const gate = deferred<void>();
      gates.set(input.threadId, gate);
      yield ev({
        type: EventType.TEXT_MESSAGE_START,
        messageId: `assistant:${input.threadId}`,
        role: "assistant",
      });
      yield ev({
        type: EventType.TEXT_MESSAGE_CONTENT,
        messageId: `assistant:${input.threadId}`,
        delta: input.text,
      });
      await gate.promise;
      yield ev({ type: EventType.RUN_FINISHED, threadId: input.threadId, runId: input.runId });
    })());

    const first = useConversationStore.getState().send("A reply");
    expect(first.accepted).toBe(true);
    const firstSession = useConversationStore.getState().currentSessionId!;

    useConversationStore.getState().newSession();
    const second = useConversationStore.getState().send("B reply");
    expect(second.accepted).toBe(true);
    const secondSession = useConversationStore.getState().currentSessionId!;
    expect(secondSession).not.toBe(firstSession);

    await vi.waitFor(() => expect(gates.size).toBe(2));
    gates.get(secondSession)!.resolve();
    gates.get(firstSession)!.resolve();
    if (first.accepted) await first.completion;
    if (second.accepted) await second.completion;

    const state = useConversationStore.getState();
    expect(state.sessionViews[firstSession].messages.at(-1)?.blocks).toMatchObject([
      { kind: "text", text: "A reply" },
    ]);
    expect(state.sessionViews[secondSession].messages.at(-1)?.blocks).toMatchObject([
      { kind: "text", text: "B reply" },
    ]);
  });

  it("服务端 admission reject 移除乐观消息，供 Composer 恢复 submission", async () => {
    mockGet.mockReset();
    mockGet.mockImplementation(async (sessionId) => ({
      session_id: sessionId,
      title: sessionId,
      persona: "default",
      model: "test",
      events: [],
      active_events: [],
      revision: 0,
    }));
    mockStream.mockImplementation(async function* () {
      yield ev({ type: EventType.RUN_FINISHED, threadId: "t", runId: "r" });
    });
    mockRunGet.mockImplementationOnce(async (runId) => ({
      run_id: runId,
      session_id: useConversationStore.getState().currentSessionId!,
      status: "terminal",
      outcome: "rejected",
      reason_code: "interactive_busy",
      channel: "text",
      origin: "ag_ui",
      control_scope: "desktop_text",
      created_at: "2026-07-25T00:00:00Z",
      started_at: null,
      finished_at: "2026-07-25T00:00:00Z",
      version: 2,
    }));

    const outcome = await finishAcceptedSend(useConversationStore.getState().send("重复发送"));

    expect(outcome).toBe("rejected");
    expect(useConversationStore.getState().messages).toEqual([]);
  });

  it("transport 断线且 run pull 暂时失败时保持 session busy，等待重连校准", async () => {
    mockStream.mockImplementation(() => {
      throw new Error("connection reset");
    });
    mockRunGet.mockRejectedValueOnce(new Error("bridge offline"));

    const result = useConversationStore.getState().send("继续后台运行");
    expect(result.accepted).toBe(true);
    if (!result.accepted) return;
    expect(await result.completion).toBe("failed");

    expect(useConversationStore.getState().streaming).toBe(true);
    expect(useConversationStore.getState().activeRunIdBySession[result.sessionId]).toBe(
      result.runId,
    );
    expect(useConversationStore.getState().send("不应重复接纳")).toEqual({
      accepted: false,
      reason: "busy",
    });
  });

  it("打开历史会话时优先投影 active_events", async () => {
    mockGet.mockResolvedValue({
      session_id: "sid-1",
      title: "sid-1",
      persona: "default",
      model: "test",
      events: [
        {
          type: "user_message",
          uuid: "old-user",
          ts: "2026-06-10T00:00:00Z",
          payload: { content: "旧问题" },
        },
      ],
      active_events: [
        {
          type: "user_message",
          uuid: "new-user",
          ts: "2026-06-10T00:00:01Z",
          payload: { content: "新问题" },
        },
      ],
    });

    await useConversationStore.getState().openSession("sid-1");

    expect(firstText()).toBe("新问题");
  });

  it("打开历史会话时支持 active-only 详情响应", async () => {
    mockGet.mockResolvedValue({
      session_id: "sid-active-only",
      title: "sid-active-only",
      persona: "default",
      model: "test",
      active_events: [
        {
          type: "assistant_message",
          uuid: "active-assistant",
          ts: "2026-06-10T00:00:00Z",
          payload: { content: "只返回有效事件", partial: false },
        },
      ],
    });

    await useConversationStore.getState().openSession("sid-active-only");

    expect(firstText()).toBe("只返回有效事件");
  });

  it("编辑重发最后一条 user 后按 active projection rehydrate", async () => {
    mockGet
      .mockResolvedValueOnce({
        session_id: "sid-1",
        title: "sid-1",
        persona: "default",
        model: "test",
        events: [
          {
            type: "user_message",
            uuid: "old-user",
            ts: "2026-06-10T00:00:00Z",
            payload: { content: "旧问题" },
          },
          {
            type: "assistant_message",
            uuid: "old-assistant",
            ts: "2026-06-10T00:00:01Z",
            payload: { content: "旧回答", partial: false },
          },
        ],
      })
      .mockResolvedValueOnce({
        session_id: "sid-1",
        title: "sid-1",
        persona: "default",
        model: "test",
        events: [],
        active_events: [
          {
            type: "user_message",
            uuid: "new-user",
            ts: "2026-06-10T00:00:02Z",
            payload: { content: "新问题" },
          },
          {
            type: "assistant_message",
            uuid: "new-assistant",
            ts: "2026-06-10T00:00:03Z",
            payload: { content: "新回答", partial: false },
          },
        ],
      });
    mockEditStream.mockImplementation(async function* () {
      yield ev({ type: EventType.RUN_FINISHED, threadId: "sid-1", runId: "r" });
    });

    await useConversationStore.getState().openSession("sid-1");
    const oldUserId = useConversationStore.getState().messages[0]?.id;
    expect(oldUserId).toBeTruthy();
    await useConversationStore.getState().editResendLatest(oldUserId!, "新问题");

    expect(mockEditStream).toHaveBeenCalledWith(
      {
        sessionId: "sid-1",
        runId: expect.any(String),
        text: "新问题",
        expectedUserContent: "旧问题",
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(messageTexts()).toEqual(["新问题", "新回答"]);
  });

  it("编辑重发 RUN_ERROR 已落盘时不重复追加临时错误", async () => {
    const errorText = "模型服务这会儿过载了，这轮没有生成完整回答。稍后可以重试。";
    mockGet
      .mockResolvedValueOnce({
        session_id: "sid-1",
        title: "sid-1",
        persona: "default",
        model: "test",
        events: [
          {
            type: "user_message",
            uuid: "old-user",
            ts: "2026-06-10T00:00:00Z",
            payload: { content: "旧问题" },
          },
        ],
      })
      .mockResolvedValueOnce({
        session_id: "sid-1",
        title: "sid-1",
        persona: "default",
        model: "test",
        events: [],
        active_events: [
          {
            type: "user_message",
            uuid: "new-user",
            ts: "2026-06-10T00:00:02Z",
            payload: { content: "新问题" },
          },
          {
            type: "run_error",
            uuid: "run-error",
            ts: "2026-06-10T00:00:03Z",
            payload: { code: "rate_limit", message: errorText, recoverable: true },
          },
        ],
      });
    mockEditStream.mockImplementation(async function* () {
      yield ev({ type: EventType.RUN_ERROR, message: errorText, code: "rate_limit" });
    });

    await useConversationStore.getState().openSession("sid-1");
    const oldUserId = useConversationStore.getState().messages[0]?.id;
    await useConversationStore.getState().editResendLatest(oldUserId!, "新问题");

    const errors = useConversationStore
      .getState()
      .messages.filter((message) => message.status === "error");
    expect(errors).toHaveLength(1);
    expect(errors[0].error).toBe(errorText);
    expect(errors[0].errorCode).toBe("rate_limit");
  });
});

describe("conversation store · continue tool result", () => {
  it("复用完成的工具结果继续生成，不插入新的用户消息", async () => {
    mockGet.mockResolvedValueOnce({
      session_id: "sid-1",
      title: "sid-1",
      persona: "default",
      model: "test",
      events: [
        {
          type: "user_message",
          uuid: "user-1",
          ts: "2026-06-10T00:00:00Z",
          payload: { content: "查一下" },
        },
        {
          type: "assistant_message",
          uuid: "assistant-tool",
          ts: "2026-06-10T00:00:01Z",
          payload: { content: "", partial: false },
        },
        {
          type: "tool_call_request",
          uuid: "tool-request",
          ts: "2026-06-10T00:00:02Z",
          payload: {
            tool_call_id: "provider-call",
            event_tool_call_id: "tool-1",
            tool_name: "web_search",
            args: { query: "latest" },
          },
        },
        {
          type: "tool_call_result",
          uuid: "tool-result",
          ts: "2026-06-10T00:00:03Z",
          payload: {
            tool_call_id: "provider-call",
            event_tool_call_id: "tool-1",
            tool_name: "web_search",
            content: "saved result",
            is_error: false,
          },
        },
        {
          type: "run_error",
          uuid: "run-error",
          ts: "2026-06-10T00:00:04Z",
          payload: {
            code: "upstream_transient",
            message: "模型服务连接不稳定",
            recoverable: true,
          },
        },
      ],
    });
    mockContinueStream.mockImplementation(async function* () {
      yield ev({
        type: EventType.TEXT_MESSAGE_START,
        messageId: "continued",
        role: "assistant",
      });
      yield ev({
        type: EventType.TEXT_MESSAGE_CONTENT,
        messageId: "continued",
        delta: "根据已有结果整理完成",
      });
      yield ev({ type: EventType.RUN_FINISHED, threadId: "sid-1", runId: "r" });
    });

    await useConversationStore.getState().openSession("sid-1");
    const before = useConversationStore.getState().messages;
    const errorMessage = before.at(-1);
    expect(errorMessage?.status).toBe("error");

    await useConversationStore.getState().continueToolResult(errorMessage!.id);

    expect(mockContinueStream).toHaveBeenCalledWith(
      "sid-1",
      expect.any(String),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(useConversationStore.getState().messages.filter((m) => m.role === "user")).toHaveLength(1);
    expect(messageTexts().at(-1)).toBe("根据已有结果整理完成");
  });
});

describe("conversation store · background detail refresh", () => {
  it("先收到 external active run，再打开 detail 时仍保持 busy", async () => {
    useConversationStore.getState().applyRunSnapshot({
      run_id: "external-run",
      session_id: "sid-external",
      status: "running",
      outcome: null,
      reason_code: "",
      channel: "voice",
      origin: "openai",
      control_scope: "voice",
      created_at: "2026-07-25T00:00:00Z",
      started_at: "2026-07-25T00:00:01Z",
      finished_at: null,
      version: 2,
    });
    mockGet.mockResolvedValueOnce(sessionDetail("sid-external", "external partial"));

    await useConversationStore.getState().openSession("sid-external");

    expect(useConversationStore.getState().streaming).toBe(true);
    expect(useConversationStore.getState().send("不应接纳")).toEqual({
      accepted: false,
      reason: "busy",
    });
  });

  it("重连 pull 中缺失的旧 active run 会解除 busy 并刷新当前 detail", async () => {
    useConversationStore.setState({
      currentSessionId: "sid-stale",
      streaming: true,
      activeRunIdBySession: { "sid-stale": "stale-run" },
      sessionViews: {
        "sid-stale": {
          messages: [],
          currentModel: "test",
          streaming: true,
          historyLoading: false,
          historyLoadSeq: 0,
          syncRefreshSeq: 0,
          revision: 0,
        },
      },
    });
    mockRunList.mockResolvedValueOnce({ runs: [] });
    mockGet.mockResolvedValueOnce(sessionDetail("sid-stale", "recovered"));

    await useConversationStore.getState().calibrateRuns();

    expect(useConversationStore.getState().streaming).toBe(false);
    expect(useConversationStore.getState().activeRunIdBySession).toEqual({});
    await vi.waitFor(() => expect(firstText()).toBe("recovered"));
  });

  it("反序返回时旧 refresh 不覆盖新结果", async () => {
    mockGet.mockResolvedValueOnce(sessionDetail("sid-1", "initial"));
    await useConversationStore.getState().openSession("sid-1");

    const older = deferred<SessionDetail>();
    const newer = deferred<SessionDetail>();
    mockGet.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const olderRun = useConversationStore.getState().refreshCurrentSession("sid-1");
    const newerRun = useConversationStore.getState().refreshCurrentSession("sid-1");
    newer.resolve(sessionDetail("sid-1", "newest"));
    expect(await newerRun).toBe("applied");
    older.resolve(sessionDetail("sid-1", "old"));
    expect(await olderRun).toBe("stale");
    expect(firstText()).toBe("newest");
  });

  it("切换 session 后旧 refresh 返回不会污染新会话", async () => {
    mockGet.mockResolvedValueOnce(sessionDetail("sid-1", "one"));
    await useConversationStore.getState().openSession("sid-1");
    const pending = deferred<SessionDetail>();
    mockGet.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(sessionDetail("sid-2", "two"));
    const refresh = useConversationStore.getState().refreshCurrentSession("sid-1");
    await useConversationStore.getState().openSession("sid-2");
    pending.resolve(sessionDetail("sid-1", "late"));
    expect(await refresh).toBe("stale");
    expect(useConversationStore.getState().currentSessionId).toBe("sid-2");
    expect(firstText()).toBe("two");
  });

  it("streaming 时拒绝后台 detail 替换", async () => {
    useConversationStore.setState({ currentSessionId: "sid-1", streaming: true });
    expect(await useConversationStore.getState().refreshCurrentSession("sid-1")).toBe("stale");
    expect(mockGet).not.toHaveBeenCalled();
  });
});

describe("conversation store · bounded caches", () => {
  it("只驱逐超出 20 个的空闲 view，保留 current、active 和非空草稿 session", () => {
    const emptyView = {
      messages: [],
      currentModel: null,
      streaming: false,
      historyLoading: false,
      historyLoadSeq: 0,
      syncRefreshSeq: 0,
      revision: 0,
    };
    const idleEntries = Object.fromEntries(
      Array.from({ length: 25 }, (_, index) => [`idle-${index}`, { ...emptyView }]),
    );
    useConversationStore.setState({
      currentSessionId: "current",
      sessionViews: {
        ...idleEntries,
        current: { ...emptyView },
        active: { ...emptyView, streaming: true },
        draft: { ...emptyView },
      },
      activeRunIdBySession: { active: "run-active" },
      viewAccessedAt: Object.fromEntries(
        Array.from({ length: 25 }, (_, index) => [`idle-${index}`, index]),
      ),
    });
    useDraftStore.getState().setText("draft", "未发送内容");

    useConversationStore.getState().pruneCaches();

    const views = useConversationStore.getState().sessionViews;
    expect(Object.keys(views).filter((key) => key.startsWith("idle-"))).toHaveLength(20);
    expect(views.current).toBeDefined();
    expect(views.active).toBeDefined();
    expect(views.draft).toBeDefined();
  });
});

describe("conversation store · session model selection", () => {
  it("新会话先校验 pending model，并把同一模型传给首次 run", async () => {
    mockStream.mockImplementation(async function* () {
      yield ev({ type: EventType.RUN_FINISHED });
    });

    const selection = await useConversationStore.getState().selectModel("kimi/kimi-k2.6");
    expect(selection).toEqual({ status: "applied", model: "kimi/kimi-k2.6" });
    expect(mockValidateModel).toHaveBeenCalledWith("kimi/kimi-k2.6");
    expect(useConversationStore.getState().currentModel).toBe("kimi/kimi-k2.6");

    await finishAcceptedSend(useConversationStore.getState().send("hello"));
    expect(mockStream).toHaveBeenCalledWith(
      expect.objectContaining({ model: "kimi/kimi-k2.6" }),
      expect.any(Object),
    );
  });

  it("新会话模型校验失败时保持原 pending model", async () => {
    mockValidateModel.mockRejectedValueOnce({
      friendly: "模型凭据尚未配置",
      code: "credential_missing",
    });

    const result = await useConversationStore.getState().selectModel("kimi/kimi-k2.6");
    expect(result).toEqual({
      status: "failed",
      error: { friendly: "模型凭据尚未配置", code: "credential_missing" },
    });
    expect(useConversationStore.getState().currentModel).toBeNull();
  });

  it("已有会话只在后端成功后提交 effective model", async () => {
    mockGet.mockResolvedValueOnce(sessionDetail("sid-1", "hello", "deepseek/old"));
    await useConversationStore.getState().openSession("sid-1");
    const pending = deferred<{ session_id: string; model: string }>();
    mockSwitchModel.mockReturnValueOnce(pending.promise);

    const switching = useConversationStore.getState().selectModel("kimi/kimi-k2.6");
    expect(useConversationStore.getState().currentModel).toBe("deepseek/old");
    expect(useConversationStore.getState().modelSwitching).toBe(true);
    pending.resolve({ session_id: "sid-1", model: "kimi/kimi-k2.6" });

    expect(await switching).toEqual({ status: "applied", model: "kimi/kimi-k2.6" });
    expect(useConversationStore.getState().currentModel).toBe("kimi/kimi-k2.6");
    expect(useConversationStore.getState().modelSwitching).toBe(false);
  });

  it("连续切换的迟到响应不会覆盖最后一次选择", async () => {
    mockGet.mockResolvedValueOnce(sessionDetail("sid-1", "hello", "deepseek/old"));
    await useConversationStore.getState().openSession("sid-1");
    const older = deferred<{ session_id: string; model: string }>();
    const newer = deferred<{ session_id: string; model: string }>();
    mockSwitchModel.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);

    const olderRun = useConversationStore.getState().selectModel("kimi/older");
    const newerRun = useConversationStore.getState().selectModel("kimi/newer");
    newer.resolve({ session_id: "sid-1", model: "kimi/newer" });
    expect(await newerRun).toEqual({ status: "applied", model: "kimi/newer" });
    older.resolve({ session_id: "sid-1", model: "kimi/older" });
    expect(await olderRun).toEqual({ status: "stale" });
    expect(useConversationStore.getState().currentModel).toBe("kimi/newer");
  });

  it("后台 refresh 同步后端模型，但旧 refresh 不覆盖进行中的切换", async () => {
    mockGet.mockResolvedValueOnce(sessionDetail("sid-1", "hello", "deepseek/old"));
    await useConversationStore.getState().openSession("sid-1");
    mockGet.mockResolvedValueOnce(sessionDetail("sid-1", "refreshed", "kimi/external"));
    expect(await useConversationStore.getState().refreshCurrentSession("sid-1")).toBe("applied");
    expect(useConversationStore.getState().currentModel).toBe("kimi/external");

    const refreshDetail = deferred<SessionDetail>();
    const switchResponse = deferred<{ session_id: string; model: string }>();
    mockGet.mockReturnValueOnce(refreshDetail.promise);
    mockSwitchModel.mockReturnValueOnce(switchResponse.promise);
    const refresh = useConversationStore.getState().refreshCurrentSession("sid-1");
    const switching = useConversationStore.getState().selectModel("kimi/final");
    refreshDetail.resolve(sessionDetail("sid-1", "late", "deepseek/stale"));
    expect(await refresh).toBe("stale");
    switchResponse.resolve({ session_id: "sid-1", model: "kimi/final" });
    expect(await switching).toEqual({ status: "applied", model: "kimi/final" });
    expect(useConversationStore.getState().currentModel).toBe("kimi/final");
  });

  it("切换和编辑状态阻止发送", () => {
    useConversationStore.setState({ modelSwitching: true });
    expect(useConversationStore.getState().send("hello")).toEqual({
      accepted: false,
      reason: "model-switching",
    });
    useConversationStore.setState({ modelSwitching: false, editingMessageId: "user-1" });
    expect(useConversationStore.getState().send("hello")).toEqual({
      accepted: false,
      reason: "editing",
    });
  });
});
