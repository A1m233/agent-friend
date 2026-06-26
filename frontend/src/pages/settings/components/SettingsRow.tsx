import type { ReactNode } from "react";
import { Info } from "lucide-react";
import {
  Button,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui";

interface SettingsRowProps {
  label: string;
  tooltip?: ReactNode;
  tooltipAriaLabel?: string;
  children: ReactNode;
}

export function SettingsRow({
  label,
  tooltip,
  tooltipAriaLabel,
  children,
}: SettingsRowProps) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-sm">{label}</span>
        {tooltip ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                className="size-6 text-muted"
                aria-label={tooltipAriaLabel ?? `${label}说明`}
              >
                <Info className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs" side="top">
              {tooltip}
            </TooltipContent>
          </Tooltip>
        ) : null}
      </div>
      {children}
    </div>
  );
}
