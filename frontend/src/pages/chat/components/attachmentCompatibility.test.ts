import { describe, expect, it } from "vitest";
import type { AttachmentInputCapabilities, ModelCapabilities } from "@/lib/model-config";
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
  it("accepts session PDFs on a text model without accepting native images", () => {
    const files: AttachmentInputCapabilities = {
      attachments: true, acceptedMediaTypes: ["application/pdf"],
      maxAttachmentBytes: 100, maxAttachmentsPerTurn: 10, maxTotalAttachmentBytes: 100,
      maxAttachmentBytesByType: { "application/pdf": 100 },
      nativeMaxAttachmentsPerTurn: 0, nativeMaxTotalAttachmentBytes: 0,
      deliveryByMediaType: { "application/pdf": "session_file" },
    };
    expect(evaluateAttachmentCompatibility([{ name: "large.pdf", mediaType: "application/pdf", size: 80 }], files).compatible).toBe(true);
    expect(evaluateAttachmentCompatibility([{ name: "image.png", mediaType: "image/png", size: 1 }], files).compatible).toBe(false);
  });

  it("enforces native subset limits without charging PDF files to the provider", () => {
    const files: AttachmentInputCapabilities = {
      attachments: true, acceptedMediaTypes: ["application/pdf", "image/png"],
      maxAttachmentBytes: 100, maxAttachmentsPerTurn: 10, maxTotalAttachmentBytes: 100,
      maxAttachmentBytesByType: { "application/pdf": 100, "image/png": 5 },
      nativeMaxAttachmentsPerTurn: 1, nativeMaxTotalAttachmentBytes: 5,
      deliveryByMediaType: { "application/pdf": "session_file", "image/png": "native" },
    };
    const pdf = { name: "large.pdf", mediaType: "application/pdf", size: 80 };
    const image = { name: "image.png", mediaType: "image/png", size: 4 };
    expect(evaluateAttachmentCompatibility([pdf, image], files).compatible).toBe(true);
    expect(evaluateAttachmentCompatibility([image, image], files).compatible).toBe(false);
    expect(evaluateAttachmentCompatibility([{ ...image, size: 6 }], files).compatible).toBe(false);
  });

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
