import { ImageViewer } from "tdesign-react";
import "tdesign-react/es/image-viewer/style/css.js";

export interface AttachmentImagePreview {
  name: string;
  url: string;
}

interface AttachmentImageViewerProps {
  preview: AttachmentImagePreview | null;
  onClose: () => void;
}

export function AttachmentImageViewer({
  preview,
  onClose,
}: AttachmentImageViewerProps) {
  return (
    <ImageViewer
      visible={preview !== null}
      images={preview ? [{ mainImage: preview.url, download: false }] : []}
      title={preview?.name}
      trigger={() => null}
      closeOnOverlay
      onClose={onClose}
    />
  );
}
