import { useState } from "react";
import { Button } from "@/components/ui";
import { attachmentAssetUrl } from "@/services";
import type { ChatAttachment } from "@/types/chat";
import { AttachmentImageViewer, type AttachmentImagePreview } from "./AttachmentImageViewer";

function ImageThumbnail({ image }: { image: ChatAttachment }) {
  const [unavailable, setUnavailable] = useState(false);
  const [preview, setPreview] = useState<AttachmentImagePreview | null>(null);
  const url = attachmentAssetUrl(image.assetId);
  return (
    <div className="space-y-2">
      <p className="break-all text-xs text-muted">{image.displayName}</p>
      {unavailable ? <p role="status" className="text-xs text-muted">图片不可用，原查看记录已保留。</p> : (
        <Button
          variant="ghost"
          className="h-auto max-w-full p-0"
          aria-label={`放大查看 ${image.displayName}`}
          onClick={() => setPreview({ name: image.displayName, url })}
        >
          <img
            src={url}
            alt={image.displayName}
            className="max-h-52 max-w-full rounded object-contain"
            onError={() => { setUnavailable(true); setPreview(null); }}
          />
        </Button>
      )}
      <AttachmentImageViewer preview={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

export function ToolResultImages({ images }: {
  images: ChatAttachment[];
}) {
  return (
    <div className="space-y-3">
      {images.map((image) => <ImageThumbnail key={image.assetId} image={image} />)}
    </div>
  );
}
