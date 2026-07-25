import * as React from "react";
import { useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/utils/cn";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { observeTextOverflow } from "./overflow";

interface TextEllipsisProps extends React.ComponentProps<"span"> {
  children: string;
  showTooltip?: boolean;
}

function TextEllipsis({
  children,
  className,
  showTooltip = true,
  ...props
}: TextEllipsisProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [isOverflow, setIsOverflow] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    return observeTextOverflow(el, setIsOverflow);
  }, [children]);

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
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          {text}
        </TooltipTrigger>
        {isOverflow ? (
          <TooltipContent side="top">
            {children}
          </TooltipContent>
        ) : null}
      </Tooltip>
    </TooltipProvider>
  );
}

export { TextEllipsis };
