import type { ImageBlock } from "@/types/chat";
import { ToolResultImages } from "./ToolResultImages";

export function GeneratedImage({ block, running }: { block: ImageBlock; running: boolean }) {
  if (block.status === "pending") return <p role="status" className="text-sm text-muted">
    {running ? "正在生成图片…" : "图片生成未完成。"}
  </p>;
  if (block.status === "failed") return <p role="status" className="text-sm text-muted">
    图片未能接收或保存，请重试。已取得的其它内容仍保留。
  </p>;
  return <div className="space-y-2" data-quote-exclude="true">
    <ToolResultImages images={block.images} showNames={false} fileActions />
  </div>;
}
