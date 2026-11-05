import type { ImageBlock } from "@/types/chat";
import { ToolResultImages } from "./ToolResultImages";

function failureMessage(errorCode?: string): string {
  switch (errorCode) {
    case "image_interrupted": return "图片生成已中断，未取得完整图片。";
    case "image_storage_failed": return "图片未能保存，请重试。";
    case "image_store_unavailable": return "图片保存服务不可用，请稍后重试。";
    default: return "未能取得可用图片，请重试。";
  }
}

export function GeneratedImage({ block, running }: { block: ImageBlock; running: boolean }) {
  if (block.status === "pending") return <p role="status" className="text-sm text-muted">
    {running ? "正在生成图片…" : "图片生成未完成。"}
  </p>;
  if (block.status === "failed") return <p role="status" className="text-sm text-muted">
    {failureMessage(block.errorCode)}已取得的其它内容仍保留。
  </p>;
  return <div className="space-y-2" data-quote-exclude="true">
    <ToolResultImages images={block.images} showNames={false} fileActions />
  </div>;
}
