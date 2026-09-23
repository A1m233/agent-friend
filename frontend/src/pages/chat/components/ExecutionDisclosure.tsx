import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Button, Separator } from "@/components/ui";
import { cn } from "@/utils/cn";

const DisclosureState = createContext<Map<string, boolean> | null>(null);

export function ExecutionDisclosureStateProvider({ children }: { children: ReactNode }) {
  const [state] = useState(() => new Map<string, boolean>());
  return <DisclosureState.Provider value={state}>{children}</DisclosureState.Provider>;
}

export function ExecutionDisclosure({ children, label, enabled, collapse, revealTarget, onDisclosureChange, disclosureId = "local", showHeader = true, interactive = true }: {
  children: (open: boolean) => ReactNode;
  label: ReactNode;
  showHeader?: boolean;
  enabled: boolean;
  interactive?: boolean;
  collapse: boolean;
  revealTarget?: string;
  onDisclosureChange?: () => void;
  disclosureId?: string;
}) {
  const viewState = useContext(DisclosureState);
  const [manualOpen, setManualOpen] = useState<boolean | undefined>(() => viewState?.get(disclosureId));
  if (!interactive && manualOpen !== undefined) setManualOpen(undefined);
  const [dismissedReveal, setDismissedReveal] = useState<string | undefined>();
  const [previousReveal, setPreviousReveal] = useState(revealTarget);
  if (previousReveal !== revealTarget) {
    setPreviousReveal(revealTarget);
    setDismissedReveal(undefined);
  }
  const searchOpen = !!revealTarget && revealTarget !== dismissedReveal;
  const open = !enabled || !interactive || searchOpen || (manualOpen ?? !collapse);
  useLayoutEffect(() => {
    if (!interactive) viewState?.delete(disclosureId);
    else if (manualOpen !== undefined) viewState?.set(disclosureId, manualOpen);
  }, [manualOpen, viewState, disclosureId, interactive]);
  return (
    <>
      {showHeader && <>{enabled && interactive ? <Button variant="ghost" size="sm" aria-expanded={open}
        aria-label={open ? "收起执行过程" : "展开执行过程"}
        className="h-auto justify-start gap-1 self-start whitespace-normal px-0.5 text-sm leading-normal font-normal text-muted hover:bg-transparent hover:text-muted has-[>svg]:px-0.5"
        onClick={() => { onDisclosureChange?.(); setDismissedReveal(revealTarget); setManualOpen(!open); }}>
        {label}
        <ChevronRight aria-hidden className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
      </Button> : <div className="px-0.5 text-sm leading-normal font-normal text-muted" data-run-status-line>{label}</div>}
      <Separator style={{ height: "var(--size-chat-run-divider)" }} data-run-separator />
      </>}
      {children(open)}
    </>
  );
}
