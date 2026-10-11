import { describe, expect, it } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import type { ChatMessage, ToolBlock } from "@/types/chat";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { toolImagesOf } from "./toolImages";

const image = { asset_id: "a".repeat(64), sha256: "a".repeat(64), kind: "image", media_type: "image/png", display_name: "screen.png", size_bytes: 80 };
const request = { type: "tool_call_request", tool_call_id: "provider", event_tool_call_id: "event", tool_name: "view_image", args: {} };
const base: ChatMessage = { id: "m", role: "assistant", createdAt: "2026-10-11T00:00:00Z", blocks: [], status: "streaming" };

describe("tool image transport", () => {
  it.each([false, true])("projects native, AG-UI and restored images consistently (error=%s)", (isError) => {
    const result = { type: "tool_call_result", tool_call_id: "provider", event_tool_call_id: "event", text: "image", content: "image", images: [image], is_error: isError };
    const started = applyPushConversationEvent(base, request, "run");
    const live = applyPushConversationEvent(started, result, "run");
    const restored = projectSessionEvents([request, result].map((event, i) => ({ type: event.type, uuid: String(i), ts: base.createdAt, payload: event })));
    let agui = applyAguiEvent(base, { type: EventType.TOOL_CALL_START, toolCallId: "event", toolCallName: "view_image" } as BaseEvent);
    agui = applyAguiEvent(agui, { type: EventType.TOOL_CALL_RESULT, messageId: "result", toolCallId: "event", content: isError ? "[error] image" : "image" } as BaseEvent);
    agui = applyAguiEvent(agui, { type: EventType.CUSTOM, name: "agent_friend.tool_images", value: { toolCallId: "event", images: [image] } } as BaseEvent);
    const expected = isError ? [] : toolImagesOf([image]);
    expect((live.blocks[0] as ToolBlock).images).toEqual(expected);
    expect((restored[0].blocks[0] as ToolBlock).images).toEqual(expected);
    expect((agui.blocks[0] as ToolBlock).images ?? []).toEqual(expected);
  });

  it("does not treat tool text, untrusted URLs or invalid asset references as images", () => {
    expect(toolImagesOf("C:/temp/screen.png")).toEqual([]);
    expect(toolImagesOf([{ ...image, asset_id: "../../file" }])).toEqual([]);
    expect(toolImagesOf([{ ...image, sha256: "b".repeat(64) }])).toEqual([]);
    expect(toolImagesOf([{ ...image, size_bytes: -1 }])).toEqual([]);
    expect(toolImagesOf([{ ...image, media_type: "image/svg+xml" }])).toEqual([]);
    expect(toolImagesOf([image, image])).toHaveLength(1);
  });
});
