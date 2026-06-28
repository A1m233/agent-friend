import { beforeEach, describe, expect, it, vi } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";

// mock services 层：只控制 runAgentStream / sessionsApi.get 的行为，验证 store 的错误兜底。
vi.mock("@/services", () => ({
  editResendLatestStream: vi.fn(),
  runAgentStream: vi.fn(),
  sessionsApi: { get: vi.fn() },
}));

vi.mock("@/lib/persistence/chatUi", () => ({
  setLastChatSessionId: vi.fn(() => Promise.resolve()),
}));

import { setLastChatSessionId } from "@/lib/persistence/chatUi";
import { editResendLatestStream, runAgentStream, sessionsApi } from "@/services";
import type { SessionDetail } from "@/types/meta";
import { useConversationStore } from "./conversation";

const mockStream = vi.mocked(runAgentStream);
const mockEditStream = vi.mocked(editResendLatestStream);
const mockGet = vi.mocked(sessionsApi.get);
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

function sessionDetail(sessionId: string, content: string): SessionDetail {
  return {
    session_id: sessionId,
    title: sessionId,
    persona: "default",
    model: "test",
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
  useConversationStore.getState().newSession();
  mockSetLastChatSessionId.mockClear();
});

describe("conversation store · 拟人化错误兜底 (AC-M3.4)", () => {
  it("流式失败（带 http 状态的技术错误）→ 用户看到拟人兜底，不漏技术细节", async () => {
    // 模拟 stream.ts 在 HTTP 非 2xx 时抛的技术错误（store 的 for-await 求值即抛、被 catch）。
    mockStream.mockImplementation(() => {
      throw new Error("ag-ui run failed: http 503");
    });

    await useConversationStore.getState().send("在吗");

    const m = lastMessage();
    expect(m.role).toBe("assistant");
    expect(m.status).toBe("error");
    expect(m.error && m.error.length).toBeGreaterThan(0);
    expect(m.error!).not.toMatch(TECH_LEAK);
    // 兜底后流式态应复位，可继续对话（"可恢复"）。
    expect(useConversationStore.getState().streaming).toBe(false);
  });

  it("RUN_ERROR 事件 → 展示 bridge 已拟人化文案，不漏技术细节", async () => {
    mockStream.mockImplementation(async function* () {
      yield ev({ type: EventType.RUN_ERROR, message: "我这边有点忙不过来，待会儿再聊好吗？" });
    });

    await useConversationStore.getState().send("hi");

    const m = lastMessage();
    expect(m.status).toBe("error");
    expect(m.error!).not.toMatch(TECH_LEAK);
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

    await useConversationStore.getState().send("hi");

    const m = lastMessage();
    expect(m.status).toBe("complete");
    expect(m.blocks.some((b) => b.kind === "text")).toBe(true);
    expect(mockSetLastChatSessionId).toHaveBeenCalledWith(
      useConversationStore.getState().currentSessionId,
    );
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
      { sessionId: "sid-1", text: "新问题", expectedUserContent: "旧问题" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(messageTexts()).toEqual(["新问题", "新回答"]);
  });
});
