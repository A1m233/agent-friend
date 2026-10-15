import { describe, expect, it } from "vitest";
import { EventType, type BaseEvent } from "@ag-ui/core";
import type { ChatMessage } from "@/types/chat";
import { applyAguiEvent, applyPushConversationEvent } from "./conversationReducer";
import { projectSessionEvents } from "./sessionProjection";
import { applyAssistantImage } from "./assistantImages";

const image = { asset_id: "a".repeat(64), sha256: "a".repeat(64), kind: "image", media_type: "image/png", display_name: "generated.png", size_bytes: 80 };
const base: ChatMessage = { id: "m", role: "assistant", runId: "run", createdAt: "2026-10-15T00:00:00Z", blocks: [], status: "streaming" };
const result = { type: "assistant_image", message_id: "picture", item_index: 1, images: [image], error_code: null };

describe("generated assistant images", () => {
  it("keeps native, AG-UI and disk projections in source order, without exposing host facts", () => {
    let live = applyPushConversationEvent(base, { type: "text_delta", message_id: "before", item_index: 0, text: "before" }, "run");
    live = applyPushConversationEvent(live, result, "run");
    live = applyPushConversationEvent(live, { type: "text_delta", message_id: "after", item_index: 2, text: "after" }, "run");
    const agui = applyAguiEvent(base, { type: EventType.CUSTOM, name: "agent_friend.assistant_image", value: result } as BaseEvent);
    expect(agui.blocks[0]).toEqual(live.blocks[1]);
    const restored = projectSessionEvents([
      { uuid: "picture", payload: { content: "internal host reference", item_index: 1, generated_image: { error_code: null }, attachments: [image] } },
      { uuid: "before", payload: { content: "before", item_index: 0 } },
      { uuid: "after", payload: { content: "after", item_index: 2 } },
    ].map((event) => ({ ...event, type: "assistant_message", ts: base.createdAt, meta: { run_id: "run" } })));
    expect(restored).toHaveLength(1);
    expect(restored[0].blocks.map((b) => b.kind)).toEqual(["text", "image", "text"]);
    expect(restored[0].blocks[1]).toEqual(live.blocks[1]);
    expect(JSON.stringify(restored)).not.toContain("internal host reference");
  });

  it("deduplicates completion and late start without reviving a stopped run", () => {
    const completed = { ...base, status: "complete" as const };
    const once = applyAssistantImage(completed, result);
    const twice = applyAssistantImage(once, result);
    expect(twice).toEqual(once);
    expect(applyAssistantImage(twice, { ...result, type: "assistant_image_start" })).toEqual(once);
    expect(once.status).toBe("complete");
    expect(applyAssistantImage(base, { ...result, images: [], error_code: "image_invalid" }).blocks[0])
      .toMatchObject({ kind: "image", status: "failed", images: [] });
  });
});
