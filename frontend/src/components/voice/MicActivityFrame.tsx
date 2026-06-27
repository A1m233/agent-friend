import type { ReactNode } from "react";

import { cn } from "@/utils/cn";
import { micActivityIntensity } from "./micActivity";

interface MicActivityFrameProps {
  active: boolean;
  children: ReactNode;
  level?: number;
  muted?: boolean;
  size?: "sm" | "lg";
  className?: string;
}

const PRIMARY_RING_CLASS: Record<1 | 2 | 3, string> = {
  1: "-inset-0.25 opacity-40",
  2: "-inset-0.5 opacity-50",
  3: "-inset-0.75 opacity-60",
};

const SECONDARY_RING_CLASS: Partial<Record<1 | 2 | 3, string>> = {
  2: "-inset-0.25 opacity-25",
  3: "-inset-0.5 opacity-30",
};

export function MicActivityFrame({
  active,
  children,
  level = 0,
  muted = false,
  size = "lg",
  className,
}: MicActivityFrameProps) {
  const intensity = active ? micActivityIntensity(level, muted) : 0;
  const primaryRingClass = intensity === 0 ? null : PRIMARY_RING_CLASS[intensity];
  const secondaryRingClass = intensity === 0 ? null : SECONDARY_RING_CLASS[intensity];

  return (
    <span
      className={cn(
        "relative flex items-center justify-center",
        size === "sm" ? "size-8" : "size-10",
        className,
      )}
    >
      {primaryRingClass && (
        <span
          className={cn(
            "pointer-events-none absolute rounded-full bg-success/20 transition-all",
            primaryRingClass,
          )}
        />
      )}
      {secondaryRingClass && (
        <span
          className={cn(
            "pointer-events-none absolute rounded-full bg-success/10 transition-all",
            secondaryRingClass,
          )}
        />
      )}
      <span className="relative z-10 flex items-center justify-center">{children}</span>
    </span>
  );
}
