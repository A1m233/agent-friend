import * as React from "react";
import { useRef, useState, useEffect } from "react";

import { cn } from "@/utils/cn";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setIsOverflow(el.scrollWidth > el.clientWidth);
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

  if (!showTooltip || !isOverflow) {
    return text;
  }

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          {text}
        </TooltipTrigger>
        <TooltipContent side="top">
          {children}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { TextEllipsis };
