import { describe, expect, it } from "vitest";
import type { AttachmentInputCapabilities } from "@/lib/model-config";
import { evaluateAttachmentCompatibility } from "./attachmentCompatibility";
const caps: AttachmentInputCapabilities = { attachments: true, acceptsAnyFile: true,
  maxAttachmentBytes: 10, maxTotalAttachmentBytes: 15, maxAttachmentsPerTurn: 2 };
describe("application file admission", () => {
  it.each(["image/png", "application/zip", "text/plain", "application/octet-stream", ""])("accepts %s without native modality declarations", (mediaType) => {
    expect(evaluateAttachmentCompatibility([{name: "input", mediaType, size: 5}], caps).compatible).toBe(true);
  });
  it("retains size, count, total and environment limits", () => {
    const file = {name: "input", mediaType: "", size: 8};
    expect(evaluateAttachmentCompatibility([{...file, size: 11}], caps).compatible).toBe(false);
    expect(evaluateAttachmentCompatibility([file, file], caps).compatible).toBe(false);
    expect(evaluateAttachmentCompatibility([file, file, file], caps).compatible).toBe(false);
    expect(evaluateAttachmentCompatibility([file], null).compatible).toBe(false);
    expect(evaluateAttachmentCompatibility([], null).compatible).toBe(true);
  });
});
