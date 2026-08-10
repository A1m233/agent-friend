import type { TdAttachmentItem } from "@tdesign-react/chat";
import { resolveBridgeUrl } from "@/constants";
import {
  attachmentAssetUrl,
  type AttachmentDraft,
  type EditResendAttachmentInput,
} from "@/services";
import type { DraftAttachmentState } from "@/stores/drafts";
import type { ChatAttachment } from "@/types/chat";

function fallbackMediaType(file: File): string {
  if (file.type) return file.type;
  return file.name.toLowerCase().endsWith(".txt")
    ? "text/plain"
    : "application/octet-stream";
}

export function draftAttachmentName(item: DraftAttachmentState): string {
  return item.status === "ready" && item.source === "existing"
    ? item.attachment.displayName
    : item.file.name;
}

export function draftAttachmentMediaType(item: DraftAttachmentState): string {
  return item.status === "ready" && item.source === "existing"
    ? item.attachment.mediaType
    : fallbackMediaType(item.file);
}

export function draftAttachmentSize(item: DraftAttachmentState): number {
  return item.status === "ready" && item.source === "existing"
    ? item.attachment.sizeBytes
    : item.file.size;
}

export function draftAttachmentFileType(
  item: DraftAttachmentState,
): TdAttachmentItem["fileType"] {
  const type = draftAttachmentMediaType(item);
  if (type.startsWith("image/")) return "image";
  if (type === "application/pdf") return "pdf";
  return "txt";
}

export function draftAttachmentUrl(item: DraftAttachmentState): string | undefined {
  if (item.status !== "ready") return undefined;
  return item.source === "existing"
    ? attachmentAssetUrl(item.attachment.assetId)
    : resolveBridgeUrl(item.draft.contentUrl);
}

export function toEditResendAttachment(
  item: DraftAttachmentState,
): EditResendAttachmentInput | null {
  if (item.status !== "ready") return null;
  return item.source === "existing"
    ? { source: "existing", assetId: item.attachment.assetId }
    : {
        source: "draft",
        draftId: item.draft.draftId,
        kind: item.draft.attachment.kind,
      };
}

export function toAttachmentDraft(item: DraftAttachmentState): AttachmentDraft | null {
  return item.status === "ready" && item.source === "draft" ? item.draft : null;
}

export function toChatAttachment(item: DraftAttachmentState): ChatAttachment | null {
  if (item.status !== "ready") return null;
  const attachment = item.source === "existing" ? item.attachment : item.draft.attachment;
  return {
    assetId: attachment.assetId,
    displayName: attachment.displayName,
    kind: attachment.kind,
    mediaType: attachment.mediaType,
    sizeBytes: attachment.sizeBytes,
  };
}
