// shadcn/ui via `pnpm dlx shadcn@latest add spinner`; adapted to folder export.
import { Loader2Icon } from "lucide-react";

import { cn } from "@/utils/cn";

function Spinner({ className, ...props }: React.ComponentProps<"svg">) {
  return (
    <Loader2Icon
      role="status"
      aria-label="Loading"
      className={cn("size-4 animate-spin", className)}
      {...props}
    />
  );
}

export { Spinner };
