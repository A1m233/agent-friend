import * as React from "react"
import { ScrollArea as ScrollAreaPrimitive } from "radix-ui"

import { cn } from "@/utils/cn"

type ScrollAreaViewportProps = React.ComponentProps<
  typeof ScrollAreaPrimitive.Viewport
>

interface ScrollAreaProps
  extends React.ComponentProps<typeof ScrollAreaPrimitive.Root> {
  viewportClassName?: string
  viewportRef?: React.Ref<HTMLDivElement>
  onViewportScroll?: ScrollAreaViewportProps["onScroll"]
}

function ScrollArea({
  className,
  viewportClassName,
  viewportRef,
  onViewportScroll,
  children,
  ...props
}: ScrollAreaProps) {
  return (
    <ScrollAreaPrimitive.Root
      data-slot="scroll-area"
      className={cn("relative", className)}
      {...props}
    >
      <ScrollAreaPrimitive.Viewport
        ref={viewportRef}
        onScroll={onViewportScroll}
        data-slot="scroll-area-viewport"
        className={cn(
          "size-full rounded-[inherit] transition-[color,box-shadow] outline-none focus-visible:ring-[3px] focus-visible:ring-accent/50 focus-visible:outline-1",
          viewportClassName
        )}
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollBar />
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  )
}

function ScrollBar({
  className,
  orientation = "vertical",
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>) {
  return (
    <ScrollAreaPrimitive.ScrollAreaScrollbar
      data-slot="scroll-area-scrollbar"
      orientation={orientation}
      className={cn(
        "group/track flex touch-none p-px transition-colors select-none",
        orientation === "vertical" &&
          "h-full w-2.5 border-l border-l-transparent",
        orientation === "horizontal" &&
          "h-2.5 flex-col border-t border-t-transparent",
        className
      )}
      {...props}
    >
      <ScrollAreaPrimitive.ScrollAreaThumb
        data-slot="scroll-area-thumb"
        className={cn(
          "relative flex-1 after:absolute after:rounded-full after:bg-muted/55 group-hover/track:after:bg-muted",
          orientation === "vertical" ? "after:inset-y-0 after:inset-x-0.5" : "after:inset-x-0 after:inset-y-0.5"
        )}
      />
    </ScrollAreaPrimitive.ScrollAreaScrollbar>
  )
}

export { ScrollArea, ScrollBar }
