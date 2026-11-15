import { useCopyFeedback } from "@/hooks/use-copy-feedback";
import { Check, Copy } from "lucide-react";
import { TooltipButton } from "@/components/ui";
import { cn } from "@/utils/cn";

interface CopyMessageButtonProps {
  copyText: string;
}

export function CopyMessageButton({ copyText }: CopyMessageButtonProps) {
  const { status, copy } = useCopyFeedback(copyText);
  const copied = status === "copied";

  return (
    <TooltipButton
      type="button"
      icon={copied ? <Check className="size-[var(--text-xs)]" /> : <Copy className="size-[var(--text-xs)]" />}
      tooltip={copied ? "已复制" : "复制消息"}
      aria-label={copied ? "已复制" : "复制消息"}
      disabled={!copyText}
      className={cn(
        "h-5 w-5 rounded-[var(--radius-md)] text-muted hover:text-fg",
        copied && "text-success hover:text-success",
      )}
      onClick={() => void copy()}
    />
  );
}
