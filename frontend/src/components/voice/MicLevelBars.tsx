import { cn } from "@/utils/cn";
import { micActivityIntensity } from "./micActivity";

interface MicLevelBarsProps {
  level: number;
  active?: boolean;
  muted?: boolean;
  compact?: boolean;
}

export function voiceVolumeLabel(level: number): string {
  const intensity = micActivityIntensity(level);
  if (intensity === 3) return "音量清晰";
  if (intensity > 0) return "正在收音";
  return "等待声音";
}

export function MicLevelBars({
  level,
  active = false,
  muted = false,
  compact = false,
}: MicLevelBarsProps) {
  return (
    <span className={cn("flex items-end gap-1", compact ? "h-4" : "h-8")}>
      {[0, 1, 2].map((i) => {
        const on = !muted && (level > i * 28 || active);
        return (
          <span
            key={i}
            className={cn(
              "block rounded-full transition-all",
              compact ? "w-1" : "w-3",
              compact ? (on ? "h-4" : "h-2") : on ? "h-8" : "h-5",
              on ? "bg-current" : "bg-border",
            )}
          />
        );
      })}
    </span>
  );
}
