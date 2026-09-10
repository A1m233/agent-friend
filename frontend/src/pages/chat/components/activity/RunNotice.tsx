import { Children, type ReactNode } from "react";
import { Info } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui";

/** Terminal run feedback is system text, separate from the assistant's Markdown. */
export function RunNotice({ message, children, outcome = false }: {
  message: string;
  children?: ReactNode;
  outcome?: boolean;
}) {
  return (
    <Alert role="status" data-run-notice data-run-outcome={outcome || undefined}
      className="min-w-0 rounded-xl border-border bg-surface px-3 py-2.5 text-muted">
      <Info aria-hidden="true" className="size-4" />
      <AlertDescription className="min-w-0 whitespace-pre-wrap break-words text-muted">
        <p>{message}</p>
        {Children.toArray(children).length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-2">{children}</div>
        )}
      </AlertDescription>
    </Alert>
  );
}
