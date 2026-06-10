import { beforeEach, describe, expect, it, vi } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";

// mock services 层：只控制 runAgentStream / sessionsApi.get 的行为，验证 store 的错误兜底。
vi.mock("@/services", () => ({
  runAgentStream: vi.fn(),
  sessionsApi: { get: vi.fn() },
}));

import { runAgentStream, sessionsApi } from "@/services";
import { useConversationStore } from "./conversation";

const mockStream = vi.mocked(runAgentStream);
const mockGet = vi.mocked(sessionsApi.get);

/** 技术细节特征：HTTP 状态码 / 英文异常字样等——拟人文案里**不应**出现任何一项。 */
const TECH_LEAK = /http|error|exception|status|\b[1-5]\d{2}\b|undefined|null|stack/i;

function ev(e: Record<string, unknown>): BaseEvent {
  return e as unknown as BaseEvent;
}

function lastMessage() {
  const { messages } = useConversationStore.getState();
  return messages[messages.length - 1];
}

beforeEach(() => {
  vi.clearAllMocks();
  useConversationStore.getState().newSession();
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
  });
});
