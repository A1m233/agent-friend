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

function AttachmentRemoveIcon() {
  return (
    <svg
      aria-hidden="true"
      className="composer-reference-card-remove-icon"
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect width="16" height="16" rx="8" fill="var(--td-text-color-primary)" />
      <path
        d="M5.87836 4.93579L7.99968 7.05711L10.121 4.93579L11.0638 5.8786L8.94248 7.99992L11.0638 10.1212L10.121 11.064L7.99968 8.94273L5.87836 11.064L4.93555 10.1212L7.05687 7.99992L4.93555 5.8786L5.87836 4.93579Z"
        fill="var(--td-bg-color-container)"
      />
    </svg>
  );
}

function sourceLabel(role: ChatTextReference["sourceRole"]): string {
  if (role === "user") return "用户消息";
  if (role === "assistant") return "Agent 消息";
  return "引用消息";
}

interface ReferenceSummaryPopoverProps {
  references: ChatTextReference[];
  layout?: "summary" | "attachment";
  removable?: boolean;
  onRemove?: (referenceId: string) => void;
  onClear?: () => void;
  onSelect?: (reference: ChatTextReference) => void;
  className?: string;
}

export function ReferenceSummaryPopover({
  references,
  layout = "summary",
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
  const attachmentLayout = layout === "attachment";

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
      className={cn(
        attachmentLayout
          ? "composer-reference-card group"
          : "flex min-w-0 items-center gap-1",
        className,
      )}
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant={attachmentLayout ? "ghost" : "outline"}
            size={attachmentLayout ? "default" : "sm"}
            className={cn(
              attachmentLayout
                ? "composer-reference-card-trigger min-w-0 text-left"
                : "max-w-full rounded-full bg-bg text-muted hover:bg-surface hover:text-fg",
            )}
            aria-label={`查看 ${references.length} 个已选文本`}
            onPointerEnter={() => {
              cancelClose();
              setOpen(true);
            }}
            onPointerLeave={scheduleClose}
            onFocus={() => {
              cancelClose();
              setOpen(true);
            }}
            onBlur={scheduleClose}
          >
            {attachmentLayout ? (
              <span className="composer-reference-card-icon" aria-hidden="true">
                <Quote />
              </span>
            ) : (
              <Quote className="size-3.5 shrink-0" />
            )}
            {attachmentLayout ? (
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-fg">
                  {references.length} 个已选文本
                </span>
                <span className="block truncate text-xs text-muted">引用内容</span>
              </span>
            ) : (
              <span className="truncate">{references.length} 个已选文本</span>
            )}
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
          size={attachmentLayout ? "icon-xs" : "icon-sm"}
          className={cn(
            "shrink-0 rounded-full text-muted hover:text-fg",
            attachmentLayout && "composer-reference-card-remove",
          )}
          aria-label="清空全部引用"
          onMouseEnter={() => {
            cancelClose();
            setOpen(false);
          }}
          onFocus={() => {
            cancelClose();
            setOpen(false);
          }}
          onClick={onClear}
        >
          {attachmentLayout ? <AttachmentRemoveIcon /> : <X className="size-3.5" />}
        </Button>
      )}
    </div>
  );
}
