/** Translate the dominant wheel axis into CSS pixels along the toolbar. */
export function actionBarWheelDistance(event: Pick<WheelEvent, "deltaX" | "deltaY" | "deltaMode" | "ctrlKey">, lineHeight: number, viewportSize: number) {
  if (event.ctrlKey) return 0;
  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
  if (!Number.isFinite(delta)) return 0;
  switch (event.deltaMode) {
    case 0: return delta;
    case 1: return delta * lineHeight;
    case 2: return delta * viewportSize;
    default: return 0;
  }
}

type WheelSample = Pick<WheelEvent, "deltaMode" | "deltaX" | "deltaY"> & {
  wheelDeltaX?: number;
  wheelDeltaY?: number;
};

const GESTURE_IDLE_MS = 160;

function isFullWheelTick(value: number) {
  const ticks = Math.round(value / 120);
  return ticks > 0 && Math.abs(value - ticks * 120) <= 1;
}

function isWheelNotch(event: WheelSample, pixelRatio: number) {
  if (event.deltaX && event.deltaY) return false;
  const delta = Math.abs(event.deltaY || event.deltaX);
  if (event.deltaMode !== 0) return Number.isInteger(delta);

  // WebViews can report a conventional mouse notch in pixel mode. Its legacy
  // tick is normally 120 units, sometimes scaled to logical pixels.
  const tick = Math.abs((event.deltaY ? event.wheelDeltaY : event.wheelDeltaX) ?? 0);
  if (!tick) return delta === 100 || delta === 120;
  return delta * pixelRatio >= 40 && (isFullWheelTick(tick) || isFullWheelTick(tick * pixelRatio));
}

/** Keep fine input continuous through the faster middle and momentum tail of a swipe. */
export function createActionBarWheelGesture() {
  let lastAt = -Infinity;
  let precise = false;
  let pagedDirection = 0;
  return (event: WheelSample, now: number, pixelRatio: number): "scroll" | "page" | "ignore" => {
    if (now - lastAt >= GESTURE_IDLE_MS || now < lastAt) {
      precise = false;
      pagedDirection = 0;
    }
    lastAt = now;
    if (!isWheelNotch(event, pixelRatio)) precise = true;
    if (precise) return "scroll";
    const direction = Math.sign(event.deltaY || event.deltaX);
    if (pagedDirection === direction) return "ignore";
    pagedDirection = direction;
    return "page";
  };
}
