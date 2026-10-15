import type { ChatMessage, ImageBlock, MessageBlock } from "@/types/chat";
import { toolImagesOf } from "./toolImages";

export function insertAssistantBlock(blocks: MessageBlock[], block: ImageBlock): MessageBlock[] {
  const result = blocks.filter((item) => !(item.kind === "image" && item.mid === block.mid));
  const next = result.findIndex((item) => (item.kind === "text" || item.kind === "image")
    && item.itemIndex !== undefined && item.itemIndex > block.itemIndex);
  if (next < 0) result.push(block);
  else result.splice(next, 0, block);
  return result;
}

export function applyAssistantImage(msg: ChatMessage, value: unknown): ChatMessage {
  if (!value || typeof value !== "object") return msg;
  const data = value as Record<string, unknown>;
  if (typeof data.message_id !== "string" || typeof data.item_index !== "number"
    || !Number.isInteger(data.item_index) || data.item_index < 0) return msg;
  const existing = msg.blocks.find((b) => b.kind === "image" && b.mid === data.message_id);
  const pending = data.type === "assistant_image_start";
  if (pending && existing) return msg;
  const images = pending ? [] : toolImagesOf(data.images);
  const errorCode = typeof data.error_code === "string" ? data.error_code : undefined;
  return { ...msg, blocks: insertAssistantBlock(msg.blocks, {
    kind: "image", mid: data.message_id, itemIndex: data.item_index,
    images, status: pending ? "pending" : errorCode || !images.length ? "failed" : "saved",
    errorCode,
  }) };
}
