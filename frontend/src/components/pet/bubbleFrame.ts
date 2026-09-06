import type { BubblePresentationState } from "@/pet/accessoryLayout/protocol";

/** Keep target content at the same screen point while the native carrier is reclaimed. */
export function bubbleFrame(state: BubblePresentationState) {
  const host = state.hostRectPhysical;
  const target = state.targetRectPhysical;
  if (!host || !target || !Number.isFinite(state.scaleFactor) || state.scaleFactor <= 0) return null;
  const ratio = (offset: number, spare: number) => spare > 0 ? Math.max(0, Math.min(1, offset / spare)) : 0;
  return {
    width: target.width / state.scaleFactor,
    height: target.height / state.scaleFactor,
    x: ratio(target.x - host.x, host.width - target.width),
    y: ratio(target.y - host.y, host.height - target.height),
  };
}
