import { useMemo, useState } from "react";
import { Attachments, type TdAttachmentItem } from "@tdesign-react/chat";
import { ImageViewer } from "tdesign-react";
import "tdesign-react/es/image-viewer/style/css.js";
import { attachmentAssetUrl } from "@/services";
import type { ChatAttachment } from "@/types/chat";

interface MessageAttachmentsProps {
  attachments: ChatAttachment[];
  unavailableAssets: Set<string>;
}

interface ImagePreview {
  name: string;
  url: string;
}

/**
 * 消息附件继续复用 TDesign Attachments；图片预览使用 TDesign React ImageViewer。
 * Chat 内置预览渲染在 filecard 的 Shadow DOM 中，会被聊天滚动区裁切。
 */
export function MessageAttachments({
  attachments,
  unavailableAssets,
}: MessageAttachmentsProps) {
  const [preview, setPreview] = useState<ImagePreview | null>(null);
  const items = useMemo<TdAttachmentItem[]>(
    () => attachments.map((item) => ({
      key: item.assetId,
      fileType:
        item.kind === "image"
          ? "image"
          : item.mediaType === "application/pdf"
            ? "pdf"
            : "txt",
      name: item.displayName,
      size: item.sizeBytes,
      url: attachmentAssetUrl(item.assetId),
      status: unavailableAssets.has(item.assetId) ? "fail" : "success",
    })),
    [attachments, unavailableAssets],
  );

  return (
    <>
      <Attachments
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
      <ImageViewer
        visible={preview !== null}
        images={preview ? [{ mainImage: preview.url, download: false }] : []}
        title={preview?.name}
        trigger={() => null}
        closeOnOverlay
        onClose={() => setPreview(null)}
      />
    </>
  );
}
