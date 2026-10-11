import type { ChatAttachment } from "@/types/chat";

/** Shared boundary for native push, AG-UI and durable tool image references. */
export function toolImagesOf(value: unknown): ChatAttachment[] {
  if (!Array.isArray(value) || value.length > 4) return [];
  const seen = new Set<string>();
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const data = item as Record<string, unknown>;
    if (
      typeof data.asset_id !== "string" || !/^[0-9a-f]{64}$/.test(data.asset_id)
      || data.sha256 !== data.asset_id || data.kind !== "image"
      || typeof data.display_name !== "string" || !data.display_name
      || typeof data.media_type !== "string"
      || !["image/png", "image/jpeg", "image/webp"].includes(data.media_type)
      || typeof data.size_bytes !== "number" || !Number.isInteger(data.size_bytes)
      || data.size_bytes <= 0 || data.size_bytes > 8 * 1024 * 1024
      || seen.has(data.asset_id)
    ) return [];
    seen.add(data.asset_id);
    return [{
      assetId: data.asset_id,
      displayName: data.display_name,
      kind: "image",
      mediaType: data.media_type,
      sizeBytes: data.size_bytes,
    }];
  });
}
