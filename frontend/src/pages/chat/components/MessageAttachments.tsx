import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Attachments, type TdAttachmentItem } from "@tdesign-react/chat";
import { attachmentAssetUrl } from "@/services";
import type { ChatAttachment } from "@/types/chat";
import {
  AttachmentImageViewer,
  type AttachmentImagePreview,
} from "./AttachmentImageViewer";

interface MessageAttachmentsProps {
  attachments: ChatAttachment[];
  unavailableAssets: Set<string>;
}

/**
 * 消息附件继续复用 TDesign Attachments；图片预览使用 TDesign React ImageViewer。
 * Chat 内置预览渲染在 filecard 的 Shadow DOM 中，会被聊天滚动区裁切。
 */
export function MessageAttachments({
  attachments,
  unavailableAssets,
}: MessageAttachmentsProps) {
  const [preview, setPreview] = useState<AttachmentImagePreview | null>(null);
  const attachmentsRef = useRef<HTMLElement | undefined>(undefined);
  const items = useMemo<TdAttachmentItem[]>(
    () => attachments.map((item) => ({
      key: item.assetId,
      fileType:
        item.kind === "image"
          ? "image"
          : item.mediaType === "application/pdf"
            ? "pdf"
            : undefined,
      name: item.displayName,
      size: item.sizeBytes,
      url: attachmentAssetUrl(item.assetId),
      status: unavailableAssets.has(item.assetId) ? "fail" : "success",
    })),
    [attachments, unavailableAssets],
  );
  useLayoutEffect(() => {
    if (!attachmentsRef.current) return;
    // Omi 会把缺失的布尔 attribute 恢复成默认 true，需要显式写入字符串 false。
    const attachments = attachmentsRef.current as HTMLElement & { update?: () => void };
    attachments.setAttribute("removable", "false");
    attachments.setAttribute("image-viewer", "false");
    attachments.update?.();
  }, [items]);

  return (
    <>
      <Attachments
        ref={attachmentsRef}
        className="chat-message-attachments"
        items={items}
        removable={false}
        imageViewer={false}
        onFileClick={(event) => {
          const item = event.detail;
          if (item.fileType !== "image" || !item.url) return;
          setPreview({ name: item.name ?? "图片预览", url: item.url });
        }}
      />
      <AttachmentImageViewer preview={preview} onClose={() => setPreview(null)} />
    </>
  );
}
