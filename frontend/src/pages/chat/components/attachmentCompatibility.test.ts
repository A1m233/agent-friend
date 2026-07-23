import { describe, expect, it } from "vitest";
import type { ModelCapabilities } from "@/lib/model-config";
import { evaluateAttachmentCompatibility } from "./attachmentCompatibility";

const CAPABILITIES: ModelCapabilities = {
  inputModalities: ["text", "image", "document"],
  acceptedMediaTypes: ["image/png", "application/pdf", "text/plain"],
  maxAttachmentBytes: 10,
  maxTotalAttachmentBytes: 15,
  maxAttachmentsPerTurn: 2,
  attachments: true,
  toolCalling: true,
  parallelToolCalls: false,
  structuredOutput: false,
  multimodalToolResults: false,
  reasoningReplay: false,
};

describe("evaluateAttachmentCompatibility", () => {
  it("keeps an empty draft sendable on a text-only model", () => {
    expect(evaluateAttachmentCompatibility([], null).compatible).toBe(true);
  });

  it("blocks existing attachments after switching to a text-only model", () => {
    const result = evaluateAttachmentCompatibility(
      [{ name: "a.png", mediaType: "image/png", size: 5 }],
      null,
    );

    expect(result.compatible).toBe(false);
    expect(result.incompatibleNames).toEqual(["a.png"]);
  });

  it("checks type, per-file size, count, and total size", () => {
    expect(
      evaluateAttachmentCompatibility(
        [{ name: "a.bin", mediaType: "application/octet-stream", size: 5 }],
        CAPABILITIES,
      ).compatible,
    ).toBe(false);
    expect(
      evaluateAttachmentCompatibility(
        [{ name: "a.png", mediaType: "image/png", size: 11 }],
        CAPABILITIES,
      ).compatible,
    ).toBe(false);
    expect(
      evaluateAttachmentCompatibility(
        [
          { name: "a.png", mediaType: "image/png", size: 1 },
          { name: "b.png", mediaType: "image/png", size: 1 },
          { name: "c.png", mediaType: "image/png", size: 1 },
        ],
        CAPABILITIES,
      ).compatible,
    ).toBe(false);
    expect(
      evaluateAttachmentCompatibility(
        [
          { name: "a.png", mediaType: "image/png", size: 8 },
          { name: "b.pdf", mediaType: "application/pdf", size: 8 },
        ],
        CAPABILITIES,
      ).compatible,
    ).toBe(false);
  });
});
