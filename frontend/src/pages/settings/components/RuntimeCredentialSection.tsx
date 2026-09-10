import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Badge, Button, Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui";
import { runtimeCredentialOperationBusy, type RuntimeCredentialGroupSnapshot } from "@/lib/runtime-config";
import { runtimeCredentialStatusLabel } from "./RuntimeCredentialGroupEditor";

export function RuntimeCredentialSection({
  id, title, description, snapshot, open, onOpenChange, children,
}: {
  id: string;
  title: string;
  description: string;
  snapshot?: RuntimeCredentialGroupSnapshot;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  const busy = snapshot ? runtimeCredentialOperationBusy(snapshot.operation)
    || snapshot.operation.phase === "awaitingConfirmation" : false;
  const needsAttention = snapshot?.operation.phase === "failed" || snapshot?.operation.phase === "applyBlocked";
  return (
    <Collapsible id={id} open={open} onOpenChange={onOpenChange} disabled={busy}
      className="border-t border-border first:border-t-0">
      <div className="flex flex-wrap items-center justify-between gap-3 py-4">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">{title}</h4>
          <p className="text-xs text-muted">{description}</p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={snapshot?.validation === "invalid" ? "destructive" : "secondary"}>
            {busy ? "处理中" : needsAttention ? "需要处理" : snapshot ? runtimeCredentialStatusLabel(snapshot) : "读取中"}
          </Badge>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" aria-label={`${open ? "收起" : "编辑"}${title}凭据`}>
              {open ? "收起" : "编辑"}
              <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
            </Button>
          </CollapsibleTrigger>
        </div>
      </div>
      <CollapsibleContent forceMount hidden={!open} className="pb-4 data-[state=closed]:hidden">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
