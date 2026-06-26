import { Copy } from "lucide-react";
import { TooltipButton, TooltipProvider } from "@/components/ui";
import type { ChatRole } from "@/types/chat";
import { cn } from "@/utils/cn";
import { formatRelativeMessageTime } from "@/utils/formatRelativeMessageTime";

interface MessageActionBarProps {
  role: ChatRole;
  createdAt: string;
  copyText: string;
}

async function copyToClipboard(text: string) {
  if (!text || !navigator.clipboard) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // 复制失败不打断对话浏览；后续有 toast 体系时再补显式反馈。
  }
}

export function MessageActionBar({ role, createdAt, copyText }: MessageActionBarProps) {
  const timeText = formatRelativeMessageTime(createdAt);

  return (
    <div
      className={cn(
        "flex h-8 w-full items-center text-xs text-muted opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100",
        role === "user" ? "justify-end" : "justify-start",
      )}
    >
      <div className={cn("flex items-center gap-2", role === "user" && "flex-row-reverse")}>
        <TooltipProvider>
          <TooltipButton
            type="button"
            icon={<Copy className="size-4" />}
            tooltip="复制消息"
            aria-label="复制消息"
            disabled={!copyText}
            className="rounded-full text-muted hover:text-fg"
            onClick={() => void copyToClipboard(copyText)}
          />
        </TooltipProvider>
        {timeText && <span className="select-none whitespace-nowrap">{timeText}</span>}
      </div>
    </div>
  );
}
