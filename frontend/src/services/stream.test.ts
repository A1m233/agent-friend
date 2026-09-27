import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/runtime", () => ({
  ensureLocalServiceReady: vi.fn(async () => undefined),
}));

import {
  buildRunAgentInput,
  continueToolResultStream,
  editResendLatestStream,
  isEventStreamContentType,
} from "./stream";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("continueToolResultStream", () => {
  it.each([undefined, "automatic", "compact_then_resume"] as const)(
    "preserves source identity and the optional %s recovery mode", async (recoveryMode) => {
      const controller = new AbortController();
      const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
        expect(JSON.parse(String(init?.body))).toEqual({
          runId: "new-run", sourceRunId: "failed-run",
          ...(recoveryMode ? { recoveryMode } : {}),
        });
        expect(init?.signal).toBe(controller.signal);
        return new Response('data: {"type":"RUN_FINISHED"}\n\n', {
          status: 200, headers: { "content-type": "text/event-stream" },
        });
      });
      vi.stubGlobal("fetch", fetchMock);
      const events = [];
      for await (const event of continueToolResultStream("session-1", "new-run", {
        sourceRunId: "failed-run", recoveryMode, signal: controller.signal,
      })) events.push(event);
      expect(events).toHaveLength(1);
      expect(fetchMock).toHaveBeenCalledOnce();
    },
  );
});

describe("isEventStreamContentType", () => {
  it("accepts SSE content types with parameters", () => {
    expect(isEventStreamContentType("text/event-stream; charset=utf-8")).toBe(true);
  });

  it("rejects packaged asset HTML", () => {
    expect(isEventStreamContentType("text/html")).toBe(false);
    expect(isEventStreamContentType(null)).toBe(false);
  });
});

describe("buildRunAgentInput", () => {
  it("lets a specialized Agent omit desktop conversation model preferences", () => {
    const payload = buildRunAgentInput({
      threadId: "persona-editor",
      runId: "run-specialized",
      text: "调整一下性格",
    });

    expect(payload.forwardedProps).not.toHaveProperty("modelConfig");
  });

  it("uses standard ordered attachment parts and preserves message identity", () => {
    const payload = buildRunAgentInput({
      threadId: "session-1",
      runId: "run-1",
      modelConfig: { model: "kimi/kimi-k2.6", reasoningPreset: "on" },
      initialApprovalMode: "session_auto",
      messageId: "user-message-1",
      text: "请分析",
      attachments: [
        {
          draftId: "draft-1",
          contentUrl: "/v1/attachments/drafts/draft-1/content",
          expiresAt: "2026-07-24T00:00:00Z",
          attachment: {
            assetId: "a".repeat(64),
            displayName: "screen.png",
            kind: "image",
            mediaType: "image/png",
            sizeBytes: 8,
            sha256: "a".repeat(64),
          },
        },
        {
          draftId: "draft-2",
          contentUrl: "/v1/attachments/drafts/draft-2/content",
          expiresAt: "2026-07-24T00:00:00Z",
          attachment: {
            assetId: "b".repeat(64),
            displayName: "notes.txt",
            kind: "document",
            mediaType: "text/plain",
            sizeBytes: 5,
            sha256: "b".repeat(64),
          },
        },
      ],
    });

    expect(payload.messages[0].id).toBe("user-message-1");
    expect(payload.runId).toBe("run-1");
    expect(payload.messages[0].content).toMatchObject([
      { type: "image" },
      { type: "document" },
      { type: "text", text: "请分析" },
    ]);
    expect(payload.forwardedProps).not.toHaveProperty("attachments");
    expect(payload.forwardedProps).toEqual({
      modelConfig: { model: "kimi/kimi-k2.6", reasoningPreset: "on" },
      initialApprovalMode: {
        mode: "session_auto",
        riskAcknowledged: true,
      },
    });
  });

  it("sends an ordered quote-only reference set through forwarded props", () => {
    const payload = buildRunAgentInput({
      threadId: "session-1",
      runId: "run-quote",
      modelConfig: { model: "kimi/kimi-k2.6", reasoningPreset: "on" },
      text: "",
      references: [
        {
          referenceId: "ref-1",
          quotedText: "被引用的回答",
          sourceRole: "assistant",
          availability: "available",
          source: {
            kind: "session_message",
            messageEventUuid: "assistant-1",
            textBlockId: "block-1",
            externalSourceKey: null,
          },
          locator: {
            kind: "text_range",
            coordinateSpace: "rendered_text_utf16_v1",
            start: 0,
            end: 7,
          },
        },
      ],
    });

    expect(payload.messages[0].content).toBe("");
    expect(payload.forwardedProps).toMatchObject({
      quotedTextReferences: [
        {
          reference_id: "ref-1",
          quoted_text: "被引用的回答",
          source_role: "assistant",
          source: {
            message_event_uuid: "assistant-1",
            text_block_id: "block-1",
          },
          locator: {
            coordinate_space: "rendered_text_utf16_v1",
            start: 0,
            end: 7,
          },
        },
      ],
    });
  });
});

describe("editResendLatestStream", () => {
  it("sends the explicit replacement set and announces admission before events", async () => {
    const order: string[] = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      order.push("fetch");
      expect(JSON.parse(String(init?.body))).toEqual({
        runId: "run-1",
        text: "changed",
        expectedUserMessageId: "user-1",
        expectedUserContent: "old",
        attachments: [
          { source: "existing", assetId: "asset-1" },
          { source: "draft", draftId: "draft-1", kind: "document" },
        ],
      });
      return new Response('data: {"type":"RUN_FINISHED"}\n\n', {
        status: 200,
        headers: { "content-type": "text/event-stream; charset=utf-8" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    for await (const _event of editResendLatestStream(
      {
        sessionId: "session-1",
        runId: "run-1",
        text: "changed",
        expectedUserMessageId: "user-1",
        expectedUserContent: "old",
        attachments: [
          { source: "existing", assetId: "asset-1" },
          { source: "draft", draftId: "draft-1", kind: "document" },
        ],
      },
      { onAdmitted: () => order.push("admitted") },
    )) {
      order.push("event");
    }

    expect(order).toEqual(["fetch", "admitted", "event"]);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("includes the final edited reference set", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toMatchObject({
        references: [{ reference_id: "ref-edit", quoted_text: "旧回答" }],
      });
      return new Response('data: {"type":"RUN_FINISHED"}\n\n', {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    for await (const _event of editResendLatestStream({
      sessionId: "session-1",
      runId: "run-edit",
      text: "改写",
      attachments: [],
      references: [
        {
          referenceId: "ref-edit",
          quotedText: "旧回答",
          sourceRole: "assistant",
          availability: "available",
          source: {
            kind: "session_message",
            messageEventUuid: "assistant-1",
            textBlockId: "block-1",
            externalSourceKey: null,
          },
          locator: {
            kind: "whole_message",
            coordinateSpace: null,
            start: null,
            end: null,
          },
        },
      ],
    })) {
      // Drain the one-event stream.
    }

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
