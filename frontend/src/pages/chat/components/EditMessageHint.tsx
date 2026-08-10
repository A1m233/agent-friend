import { ArrowDown, PencilLine } from "lucide-react";
import { Button } from "@/components/ui";

interface EditMessageHintProps {
  onFocusComposer: () => void;
}

export function EditMessageHint({ onFocusComposer }: EditMessageHintProps) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="mt-2 ml-auto flex h-auto max-w-full items-center gap-2 rounded-xl border border-border bg-surface px-3 py-2 text-left text-xs text-muted hover:text-fg"
      onClick={onFocusComposer}
    >
      <PencilLine className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0">正在编辑这条消息，请在下方输入框修改</span>
      <ArrowDown className="size-3.5 shrink-0" aria-hidden="true" />
    </Button>
  );
}
