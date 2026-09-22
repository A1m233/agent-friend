import { useEffect, useState } from "react";
import { Quote } from "lucide-react";
import { Button } from "@/components/ui";
import { useDraftStore } from "@/stores";
import type { ChatTextReference } from "@/types/chat";
import { captureQuoteSelection, type CapturedQuoteSelection } from "./messageQuoteSelection";

interface MessageQuoteSelectionActionProps {
  workspaceKey: string;
  disabled?: boolean;
  onFocusComposer?: () => void;
}

export function MessageQuoteSelectionAction({
  workspaceKey,
  disabled = false,
  onFocusComposer,
}: MessageQuoteSelectionActionProps) {
  const addReference = useDraftStore((state) => state.addReference);
  const [captured, setCaptured] = useState<CapturedQuoteSelection | null>(null);

  useEffect(() => {
    if (disabled) {
      setCaptured(null);
      return;
    }
    const update = () => setCaptured(captureQuoteSelection(window.getSelection()));
    const clear = () => setCaptured(null);
    const clearIfSelectionEmpty = () => {
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0 || !selection.toString()) clear();
    };
    window.addEventListener("pointerup", update, true);
    window.addEventListener("keyup", update, true);
    window.addEventListener("scroll", clear, true);
    document.addEventListener("selectionchange", clearIfSelectionEmpty);
    return () => {
      window.removeEventListener("pointerup", update, true);
      window.removeEventListener("keyup", update, true);
      window.removeEventListener("scroll", clear, true);
      document.removeEventListener("selectionchange", clearIfSelectionEmpty);
    };
  }, [disabled]);

  if (!captured) return null;
  const top = captured.rect.top;
  const left = captured.rect.left + captured.rect.width / 2;

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="fixed z-50 -mt-2 -translate-x-1/2 -translate-y-full rounded-full shadow-md"
      style={{ top, left }}
      onPointerDown={(event) => event.preventDefault()}
      onClick={() => {
        const reference: ChatTextReference = {
          referenceId: crypto.randomUUID(),
          quotedText: captured.quotedText,
          sourceRole: captured.sourceRole,
          availability: "available",
          source: {
            kind: "session_message",
            messageEventUuid: captured.messageEventUuid,
            textBlockId: captured.textBlockId,
            externalSourceKey: null,
          },
          locator: {
            kind: "text_range",
            coordinateSpace: "rendered_text_utf16_v1",
            start: captured.start,
            end: captured.end,
          },
        };
        addReference(workspaceKey, reference);
        window.getSelection()?.removeAllRanges();
        setCaptured(null);
        onFocusComposer?.();
      }}
    >
      <Quote className="size-3.5" />
      引用
    </Button>
  );
}
