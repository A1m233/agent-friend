import type { AttachmentInputCapabilities } from "@/lib/model-config";

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
  capabilities: AttachmentInputCapabilities | null,
): AttachmentCompatibility {
  if (attachments.length === 0) {
    return { compatible: true, incompatibleNames: [], reason: null };
  }
  if (!capabilities?.attachments) {
    return {
      compatible: false,
      incompatibleNames: attachments.map((item) => item.name),
      reason: "当前会话文件处理暂不可用",
    };
  }
  if (attachments.length > capabilities.maxAttachmentsPerTurn) {
    return {
      compatible: false,
      incompatibleNames: attachments.map((item) => item.name),
      reason: `单轮最多添加 ${capabilities.maxAttachmentsPerTurn} 个附件`,
    };
  }

  const incompatibleNames = attachments
    .filter((item) => item.size > capabilities.maxAttachmentBytes)
    .map((item) => item.name);
  if (incompatibleNames.length > 0) {
    return { compatible: false, incompatibleNames, reason: "部分附件超过文件大小限制" };
  }
  const totalBytes = attachments.reduce((total, item) => total + item.size, 0);
  if (totalBytes > capabilities.maxTotalAttachmentBytes) {
    return {
      compatible: false,
      incompatibleNames: attachments.map((item) => item.name),
      reason: "附件总大小超过限制",
    };
  }
  return { compatible: true, incompatibleNames: [], reason: null };
}
