import { Pencil } from "lucide-react";
import { TooltipButton, TooltipProvider } from "@/components/ui";
import type { ChatRole } from "@/types/chat";
import { cn } from "@/utils/cn";
import { formatRelativeMessageTime } from "@/utils/formatRelativeMessageTime";
import { CopyMessageButton } from "./CopyMessageButton";

interface MessageActionBarProps {
  role: ChatRole;
  createdAt: string;
  copyText: string;
  canEdit?: boolean;
  onEdit?: () => void;
}

export function MessageActionBar({
  role,
  createdAt,
  copyText,
  canEdit = false,
  onEdit,
}: MessageActionBarProps) {
  const timeText = formatRelativeMessageTime(createdAt);

  return (
    <div
      className={cn(
        "flex h-8 w-full items-center text-xs text-muted",
        role === "user" ? "justify-end" : "justify-start",
      )}
    >
      <div className={cn("flex items-center gap-2", role === "user" && "flex-row-reverse")}>
        <div className="flex items-center gap-1">
          <TooltipProvider>
            <CopyMessageButton copyText={copyText} />
            {canEdit && role === "user" && (
              <TooltipButton
                type="button"
                icon={<Pencil className="size-[var(--text-xs)]" />}
                tooltip="编辑并重发"
                aria-label="编辑并重发"
                className="h-5 w-5 rounded-[var(--radius-md)] text-muted hover:text-fg"
                onClick={onEdit}
              />
            )}
          </TooltipProvider>
        </div>
        {timeText && <span className="select-none whitespace-nowrap">{timeText}</span>}
      </div>
    </div>
  );
}
