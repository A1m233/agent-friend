import type { ModelCapabilities } from "@/lib/model-config";

export interface AttachmentCandidate {
  name: string;
  mediaType: string;
  size: number;
}

export interface AttachmentCompatibility {
  compatible: boolean;
  incompatibleNames: string[];
  reason: string | null;
}

export function evaluateAttachmentCompatibility(
  attachments: readonly AttachmentCandidate[],
  capabilities: ModelCapabilities | null,
): AttachmentCompatibility {
  if (attachments.length === 0) {
    return { compatible: true, incompatibleNames: [], reason: null };
  }
  if (!capabilities?.attachments) {
    return {
      compatible: false,
      incompatibleNames: attachments.map((item) => item.name),
      reason: "当前模型不支持附件",
    };
  }
  if (attachments.length > capabilities.maxAttachmentsPerTurn) {
    return {
      compatible: false,
      incompatibleNames: attachments.map((item) => item.name),
      reason: `当前模型单轮最多支持 ${capabilities.maxAttachmentsPerTurn} 个附件`,
    };
  }

  const incompatibleNames = attachments
    .filter(
      (item) =>
        !capabilities.acceptedMediaTypes.includes(item.mediaType) ||
        item.size > capabilities.maxAttachmentBytes,
    )
    .map((item) => item.name);
  if (incompatibleNames.length > 0) {
    return { compatible: false, incompatibleNames, reason: "部分附件与当前模型不兼容" };
  }
  const totalBytes = attachments.reduce((total, item) => total + item.size, 0);
  if (totalBytes > capabilities.maxTotalAttachmentBytes) {
    return {
      compatible: false,
      incompatibleNames: attachments.map((item) => item.name),
      reason: "附件总大小超过当前模型限制",
    };
  }
  return { compatible: true, incompatibleNames: [], reason: null };
}
