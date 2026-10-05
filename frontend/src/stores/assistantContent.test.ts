import { describe, expect, it } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import type { ChatMessage } from "@/types/chat";
import type { SessionEvent } from "@/types/meta";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { parseAssistantContent } from "./assistantContent";

const parts = [{ content_index: 0, kind: "refusal", start: 0, end: 2,
  annotations: [{ type: "url_citation", title: "Source", url: "https://example.com" }] }];

describe("assistant content metadata", () => {
  it("retains identical refusal and sources through pull, push and history", () => {
    const message: ChatMessage = { id: "m", role: "assistant", runId: "r", createdAt: "t",
      status: "complete", blocks: [{ kind: "text", mid: "a", text: "No" }] };
    const pull = applyAguiEvent(message, { type: EventType.CUSTOM,
      name: "agent_friend.assistant_message_metadata", value: { messageId: "a", contentParts: parts } } as BaseEvent);
    const push = applyPushConversationEvent(message, { type: "assistant_message_metadata",
      message_id: "a", content_parts: parts }, "r");
    expect(push).toEqual(pull);
    expect(push.status).toBe("complete");
    const event = (type: string, uuid: string, payload: Record<string, unknown>): SessionEvent => ({
      type, uuid, payload, meta: { run_id: "r" }, ts: "2026-10-05T00:00:00Z",
    });
    const restored = projectSessionEvents([event("user_message", "u", { content: "hi" }),
      event("assistant_message", "a", { content: "No", content_parts: parts }),
      event("run_terminal", "t", { status: "completed" })]);
    expect(restored[1].blocks).toEqual(push.blocks);
  });

  it("drops malformed persisted fields and opaque annotation values", () => {
    const clean = parseAssistantContent([{ ...parts[0], annotations: [null,
      { type: "url_citation", title: { unexpected: true }, url: "https://example.com", secret: "private" }] }]);
    expect(clean[0].annotations).toEqual([{ type: "url_citation", url: "https://example.com" }]);
    expect(parseAssistantContent([{ ...parts[0], start: -1 }, { ...parts[0], end: 0.5 }])).toEqual([]);
    expect(parseAssistantContent(undefined)).toEqual([]);
  });
});
