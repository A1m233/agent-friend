import { BRIDGE_BASE_URL } from "@/constants";
import { ensureLocalServiceReady } from "@/services/runtime";

export interface AttachmentRef {
  assetId: string;
  displayName: string;
  kind: "image" | "document";
  mediaType: string;
  sizeBytes: number;
  sha256: string;
}

export interface AttachmentDraft {
  draftId: string;
  attachment: AttachmentRef;
  contentUrl: string;
  expiresAt: string;
}

interface AttachmentUploadResponse {
  draftId: string;
  attachment: {
    asset_id: string;
    display_name: string;
    kind: "image" | "document";
    media_type: string;
    size_bytes: number;
    sha256: string;
  };
  contentUrl: string;
  expiresAt: string;
}

export async function uploadAttachment(file: File): Promise<AttachmentDraft> {
  await ensureLocalServiceReady("bridge");
  const response = await fetch(`${BRIDGE_BASE_URL}/v1/attachments/drafts`, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-Attachment-Filename": encodeURIComponent(file.name),
    },
    body: file,
  });
  const payload = (await response.json().catch(() => null)) as AttachmentUploadResponse | null;
  if (!response.ok || !payload) {
    throw new Error("attachment upload failed");
  }
  return {
    draftId: payload.draftId,
    contentUrl: payload.contentUrl,
    expiresAt: payload.expiresAt,
    attachment: {
      assetId: payload.attachment.asset_id,
      displayName: payload.attachment.display_name,
      kind: payload.attachment.kind,
      mediaType: payload.attachment.media_type,
      sizeBytes: payload.attachment.size_bytes,
      sha256: payload.attachment.sha256,
    },
  };
}

export async function releaseAttachmentDraft(draftId: string): Promise<void> {
  await ensureLocalServiceReady("bridge");
  await fetch(`${BRIDGE_BASE_URL}/v1/attachments/drafts/${encodeURIComponent(draftId)}`, {
    method: "DELETE",
  });
}

export function attachmentAssetUrl(assetId: string): string {
  return `${BRIDGE_BASE_URL}/v1/attachments/assets/${encodeURIComponent(assetId)}/content`;
}

export async function isAttachmentAssetAvailable(assetId: string): Promise<boolean> {
  try {
    const response = await fetch(
      `${BRIDGE_BASE_URL}/v1/attachments/assets/${encodeURIComponent(assetId)}`,
    );
    return response.ok;
  } catch {
    return false;
  }
}
