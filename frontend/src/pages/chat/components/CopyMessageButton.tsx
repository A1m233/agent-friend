import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { TooltipButton } from "@/components/ui";
import { cn } from "@/utils/cn";

interface CopyMessageButtonProps {
  copyText: string;
}

const COPIED_RESET_MS = 1200;

async function copyToClipboard(text: string): Promise<boolean> {
  if (!text || typeof navigator === "undefined" || !navigator.clipboard) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 复制失败不打断对话浏览；后续有 toast 体系时再补显式反馈。
    return false;
  }
}

export function CopyMessageButton({ copyText }: CopyMessageButtonProps) {
  const [copied, setCopied] = useState(false);
  const resetTimerRef = useRef<number | null>(null);

  useEffect(() => {
    setCopied(false);
  }, [copyText]);

  useEffect(
    () => () => {
      if (resetTimerRef.current) window.clearTimeout(resetTimerRef.current);
    },
    [],
  );

  const handleCopy = async () => {
    const ok = await copyToClipboard(copyText);
    if (!ok) return;
    setCopied(true);
    if (resetTimerRef.current) window.clearTimeout(resetTimerRef.current);
    resetTimerRef.current = window.setTimeout(() => {
      setCopied(false);
      resetTimerRef.current = null;
    }, COPIED_RESET_MS);
  };

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
      onClick={() => void handleCopy()}
    />
  );
}
