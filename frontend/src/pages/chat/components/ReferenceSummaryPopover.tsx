import { useEffect, useRef, useState } from "react";
import { Quote, X } from "lucide-react";
import {
  Button,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui";
import type { ChatTextReference } from "@/types/chat";
import { cn } from "@/utils/cn";

const CLOSE_DELAY_MS = 140;

function sourceLabel(role: ChatTextReference["sourceRole"]): string {
  if (role === "user") return "用户消息";
  if (role === "assistant") return "Agent 消息";
  return "引用消息";
}

interface ReferenceSummaryPopoverProps {
  references: ChatTextReference[];
  removable?: boolean;
  onRemove?: (referenceId: string) => void;
  onClear?: () => void;
  onSelect?: (reference: ChatTextReference) => void;
  className?: string;
}

export function ReferenceSummaryPopover({
  references,
  removable = false,
  onRemove,
  onClear,
  onSelect,
  className,
}: ReferenceSummaryPopoverProps) {
  const [open, setOpen] = useState(false);
  const closeTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
  }, []);

  if (references.length === 0) return null;

  const cancelClose = () => {
    if (closeTimerRef.current === null) return;
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null;
      setOpen(false);
    }, CLOSE_DELAY_MS);
  };

  return (
    <div
      className={cn("flex min-w-0 items-center gap-1", className)}
      onPointerEnter={() => {
        cancelClose();
        setOpen(true);
      }}
      onPointerLeave={scheduleClose}
      onFocusCapture={() => {
        cancelClose();
        setOpen(true);
      }}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) scheduleClose();
      }}
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="max-w-full rounded-full bg-bg text-muted hover:bg-surface hover:text-fg"
            aria-label={`查看 ${references.length} 个已选文本`}
          >
            <Quote className="size-3.5 shrink-0" />
            <span className="truncate">{references.length} 个已选文本</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent
          side="top"
          align="start"
          sideOffset={8}
          className="w-80 p-2"
          onPointerEnter={cancelClose}
          onPointerLeave={scheduleClose}
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          <div className="max-h-80 space-y-1 overflow-y-auto" role="list">
            {references.map((reference, index) => {
              const content = (
                <>
                  <span className="block text-xs font-medium text-muted">
                    {index + 1}. {sourceLabel(reference.sourceRole)}
                  </span>
                  <span className="mt-1 line-clamp-3 block whitespace-pre-wrap break-words text-sm text-fg">
                    {reference.availability === "available"
                      ? reference.quotedText
                      : "引用原文不可用"}
                  </span>
                </>
              );
              return (
                <div
                  key={reference.referenceId}
                  role="listitem"
                  className="group flex items-start gap-1 rounded-md border border-transparent p-2 hover:border-border hover:bg-bg"
                >
                  {onSelect ? (
                    <Button
                      type="button"
                      variant="ghost"
                      className="h-auto min-w-0 flex-1 justify-start p-0 text-left hover:bg-transparent"
                      onClick={() => onSelect(reference)}
                    >
                      <span className="min-w-0">{content}</span>
                    </Button>
                  ) : (
                    <div className="min-w-0 flex-1">{content}</div>
                  )}
                  {removable && onRemove && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="shrink-0 rounded-full text-muted hover:text-fg"
                      aria-label={`移除第 ${index + 1} 条引用`}
                      onClick={() => onRemove(reference.referenceId)}
                    >
                      <X className="size-3.5" />
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        </PopoverContent>
      </Popover>
      {removable && onClear && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="shrink-0 rounded-full text-muted hover:text-fg"
          aria-label="清空全部引用"
          onClick={onClear}
        >
          <X className="size-3.5" />
        </Button>
      )}
    </div>
  );
}
