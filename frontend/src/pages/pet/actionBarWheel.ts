export interface ActionBarWheelEventData {
  deltaX: number;
  deltaY: number;
  deltaMode: number;
  ctrlKey?: boolean;
}

export type ActionBarPageDirection = -1 | 0 | 1;

// Input normalization and gesture thresholds, independent of visual spacing tokens.
const WHEEL_THRESHOLD_PX = 40;
const GESTURE_IDLE_MS = 160;
const LINE_PIXELS = 16;
const PAGE_PIXELS = 800;

/** Positive dominant-axis movement advances one page; sustained momentum cannot repeat it. */
export function createActionBarWheelPager(): (
  event: ActionBarWheelEventData,
  nowMs: number,
  canPrev: boolean,
  canNext: boolean,
) => ActionBarPageDirection {
  let lastEventAt: number | null = null;
  let direction = 0;
  let accumulated = 0;
  let consumed = false;

  return (event, nowMs, canPrev, canNext) => {
    if (event.ctrlKey || ![event.deltaX, event.deltaY, nowMs].every(Number.isFinite)) return 0;
    const unit = event.deltaMode === 0 ? 1 : event.deltaMode === 1 ? LINE_PIXELS : event.deltaMode === 2 ? PAGE_PIXELS : 0;
    const delta = (Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY) * unit;
    if (!delta) return 0;

    if (lastEventAt === null || nowMs < lastEventAt || nowMs - lastEventAt >= GESTURE_IDLE_MS) {
      direction = 0;
      accumulated = 0;
      consumed = false;
    }
    lastEventAt = nowMs;
    const nextDirection = delta < 0 ? -1 : 1;
    if (nextDirection !== direction) {
      direction = nextDirection;
      accumulated = 0;
      consumed = false;
    }
    if (consumed) return 0;
    accumulated += Math.abs(delta);
    if (accumulated < WHEEL_THRESHOLD_PX) return 0;
    // Consume even a blocked edge gesture, so later page-state changes do not
    // turn the same wheel momentum into a delayed navigation.
    consumed = true;
    accumulated = 0;
    return nextDirection === -1 ? (canPrev ? -1 : 0) : (canNext ? 1 : 0);
  };
}
