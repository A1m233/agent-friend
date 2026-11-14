import { CHAT_MARKDOWN_CONTENT_PROPS } from "./chatMarkdownConfig";
import { findCaseInsensitiveOccurrences } from "./searchNavigation";
import { projectFindText } from "./findTextProjection";

export interface FindBlock { anchor: string; text: string }

/** Build once per open find bar. The inert template never mounts links or images. */
export async function prepareFindBlocks(
  blocks: Array<FindBlock & { role: "user" | "assistant" }>, signal: AbortSignal,
): Promise<FindBlock[]> {
  const { default: CherryEngine } = await import("cherry-markdown/dist/cherry-markdown.engine.core.esm.js");
  const engine = new CherryEngine(CHAT_MARKDOWN_CONTENT_PROPS.markdown.options);
  const template = document.createElement("template");
  const result: FindBlock[] = [];
  let sliceStart = performance.now();
  for (const block of blocks) {
    signal.throwIfAborted();
    if (block.role === "user") {
      result.push(block);
    } else {
      template.innerHTML = engine.makeHtml(block.text);
      result.push({ anchor: block.anchor, text: projectFindText(template.content).text });
    }
    if (performance.now() - sliceStart > 12) {
      await new Promise(resolve => setTimeout(resolve, 0));
      sliceStart = performance.now();
    }
  }
  return result;
}

export function findOccurrences(blocks: FindBlock[], query: string) {
  return blocks.flatMap(block => findCaseInsensitiveOccurrences(block.text, query)
    .map((_, occurrence) => ({ anchor: block.anchor, occurrence })));
}
