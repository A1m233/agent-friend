import * as React from "react";
import { useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/utils/cn";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { observeTextOverflow, resolveTooltipOpen } from "./overflow";

interface TextEllipsisProps extends React.ComponentProps<"span"> {
  children: string;
  showTooltip?: boolean;
  tooltipContentClassName?: string;
  tooltipDelayMs?: number;
  tooltipSide?: React.ComponentProps<typeof TooltipContent>["side"];
}

function TextEllipsis({
  children,
  className,
  showTooltip = true,
  tooltipContentClassName,
  tooltipDelayMs = 0,
  tooltipSide = "top",
  ...props
}: TextEllipsisProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [isOverflow, setIsOverflow] = useState(false);
  const [tooltipOpen, setTooltipOpen] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    return observeTextOverflow(el, setIsOverflow);
  }, [children]);

  useLayoutEffect(() => {
    if (!showTooltip || !isOverflow) {
      setTooltipOpen(false);
    }
  }, [isOverflow, showTooltip]);

  const text = (
    <span
      ref={ref}
      className={cn("block truncate", className)}
      {...props}
    >
      {children}
    </span>
  );

  if (!showTooltip) {
    return text;
  }

  return (
    <TooltipProvider delayDuration={tooltipDelayMs} skipDelayDuration={0}>
      <Tooltip
        disableHoverableContent
        open={tooltipOpen}
        onOpenChange={(requestedOpen) =>
          setTooltipOpen(resolveTooltipOpen(requestedOpen, isOverflow))
        }
      >
        <TooltipTrigger asChild>
          {text}
        </TooltipTrigger>
        {isOverflow ? (
          <TooltipContent
            className={cn("pointer-events-none", tooltipContentClassName)}
            side={tooltipSide}
          >
            {children}
          </TooltipContent>
        ) : null}
      </Tooltip>
    </TooltipProvider>
  );
}

export { TextEllipsis };
