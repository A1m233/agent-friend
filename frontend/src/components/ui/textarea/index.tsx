import * as React from "react";

import { cn } from "@/utils/cn";

/**
 * shadcn/ui Textarea（`pnpm dlx shadcn@latest add textarea` 拉取，
 * 再按项目语义颜色 token 与目录约定适配）。
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-fg shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger aria-invalid:ring-danger/20",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
