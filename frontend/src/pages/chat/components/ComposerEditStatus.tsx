import { PencilLine, X } from "lucide-react";
import { Button, Spinner, TextEllipsis } from "@/components/ui";

interface ComposerEditStatusProps {
  summary: string;
  phase: "editing" | "submitting";
  onCancel: () => void;
}

export function ComposerEditStatus({
  summary,
  phase,
  onCancel,
}: ComposerEditStatusProps) {
  const submitting = phase === "submitting";
  return (
    <div
      role="status"
      aria-live="polite"
      className="relative z-10 mx-4 flex min-w-0 items-center gap-2 rounded-t-xl bg-surface px-3 py-1.5 pr-1.5 text-xs shadow-sm"
    >
      {submitting ? (
        <Spinner className="size-3.5 shrink-0" aria-hidden="true" />
      ) : (
        <PencilLine className="size-3.5 shrink-0 text-accent" aria-hidden="true" />
      )}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="shrink-0 font-medium text-fg">
          {submitting ? "正在提交修改…" : "正在编辑之前的消息"}
        </span>
        {!submitting && (
          <TextEllipsis className="min-w-0 text-muted">{summary}</TextEllipsis>
        )}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        disabled={submitting}
        aria-label="取消编辑"
        className="shrink-0 rounded-full text-muted hover:text-fg"
        onClick={onCancel}
      >
        <X className="size-4" aria-hidden="true" />
      </Button>
    </div>
  );
}
