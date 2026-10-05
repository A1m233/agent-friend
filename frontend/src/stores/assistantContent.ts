import type { AssistantAnnotation, AssistantContentPart, ChatMessage } from "@/types/chat";

export function parseAssistantContent(value: unknown): AssistantContentPart[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((part): AssistantContentPart[] => {
    if (!part || (part.kind !== "text" && part.kind !== "refusal")
      || !Number.isSafeInteger(part.content_index) || part.content_index < 0
      || !Number.isSafeInteger(part.start) || part.start < 0
      || !Number.isSafeInteger(part.end) || part.end < part.start) return [];
    const annotations: AssistantAnnotation[] = Array.isArray(part.annotations)
      ? part.annotations.flatMap((a: unknown): AssistantAnnotation[] => {
        if (!a || typeof a !== "object" || !("type" in a) || typeof a.type !== "string") return [];
        const source = a as Record<string, unknown>;
        const clean: AssistantAnnotation = { type: a.type };
        for (const key of ["url", "title", "filename", "file_id", "container_id"] as const) {
          if (typeof source[key] === "string") clean[key] = source[key];
        }
        for (const key of ["start_index", "end_index", "index"] as const) {
          if (typeof source[key] === "number" && Number.isSafeInteger(source[key]) && source[key] >= 0) clean[key] = source[key];
        }
        return [clean];
      }) : [];
    return [{ content_index: part.content_index, kind: part.kind,
      start: part.start, end: part.end, annotations }];
  });
}

export function applyAssistantContent(msg: ChatMessage, mid: unknown, value: unknown): ChatMessage {
  if (typeof mid !== "string") return msg;
  const contentParts = parseAssistantContent(value);
  return { ...msg, blocks: msg.blocks.map(block => block.kind === "text" && block.mid === mid
    ? { ...block, contentParts } : block) };
}
