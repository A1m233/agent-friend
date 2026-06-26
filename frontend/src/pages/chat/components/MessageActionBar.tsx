import { TooltipProvider } from "@/components/ui";
import type { ChatRole } from "@/types/chat";
import { cn } from "@/utils/cn";
import { formatRelativeMessageTime } from "@/utils/formatRelativeMessageTime";
import { CopyMessageButton } from "./CopyMessageButton";

interface MessageActionBarProps {
  role: ChatRole;
  createdAt: string;
  copyText: string;
}

export function MessageActionBar({ role, createdAt, copyText }: MessageActionBarProps) {
  const timeText = formatRelativeMessageTime(createdAt);

  return (
    <div
      className={cn(
        "flex h-8 w-full items-center text-xs text-muted",
        role === "user" ? "justify-end" : "justify-start",
      )}
    >
      <div className={cn("flex items-center gap-2", role === "user" && "flex-row-reverse")}>
        <TooltipProvider>
          <CopyMessageButton copyText={copyText} />
        </TooltipProvider>
        {timeText && <span className="select-none whitespace-nowrap">{timeText}</span>}
      </div>
    </div>
  );
}
