import type { Accept, DropEvent, FileRejection } from "react-dropzone";

interface ClipboardFileSource {
  items?: ArrayLike<Pick<DataTransferItem, "kind" | "getAsFile">> | null;
  files?: ArrayLike<File> | null;
}

const EXTENSIONS_BY_MEDIA_TYPE: Readonly<Record<string, readonly string[]>> = {
  "image/jpeg": [".jpg", ".jpeg"],
  "image/png": [".png"],
  "image/webp": [".webp"],
  "application/pdf": [".pdf"],
  "text/plain": [".txt"],
};

export function extractClipboardFiles(source: ClipboardFileSource | null): File[] {
  if (!source) return [];

  const itemFiles = Array.from(source.items ?? [])
    .filter((item) => item.kind === "file")
    .flatMap((item) => {
      const file = item.getAsFile();
      return file ? [file] : [];
    });
  if (itemFiles.length > 0) return itemFiles;

  return Array.from(source.files ?? []);
}

export function dropzoneAcceptFromMediaTypes(mediaTypes: readonly string[]): Accept {
  return Object.fromEntries(
    mediaTypes.map((mediaType) => [
      mediaType,
      EXTENSIONS_BY_MEDIA_TYPE[mediaType] ?? [],
    ]),
  );
}

export function filesFromDropEvent(
  event: DropEvent,
  acceptedFiles: readonly File[],
  fileRejections: readonly FileRejection[],
): File[] {
  if ("dataTransfer" in event && event.dataTransfer?.files?.length) {
    return Array.from(event.dataTransfer.files);
  }

  const targetFiles = "target" in event
    ? (event.target as HTMLInputElement | null)?.files
    : null;
  if (targetFiles?.length) return Array.from(targetFiles);

  return [...acceptedFiles, ...fileRejections.map(({ file }) => file)];
}
