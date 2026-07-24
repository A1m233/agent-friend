import { describe, expect, it } from "vitest";
import { buildRunAgentInput, isEventStreamContentType } from "./stream";

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
  it("uses standard ordered attachment parts and preserves message identity", () => {
    const payload = buildRunAgentInput({
      threadId: "session-1",
      model: "kimi/kimi-k2.6",
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
    expect(payload.messages[0].content).toMatchObject([
      { type: "image" },
      { type: "document" },
      { type: "text", text: "请分析" },
    ]);
    expect(payload.forwardedProps).not.toHaveProperty("attachments");
    expect(payload.forwardedProps).toMatchObject({ model: "kimi/kimi-k2.6" });
  });
});
