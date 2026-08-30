interface MessageVirtualIdentity {
  id: string;
}

export const MESSAGE_VIRTUALIZER_ESTIMATED_SIZE_PX = 120;
export const MESSAGE_VIRTUALIZER_GAP_PX = 16;
export const MESSAGE_VIRTUALIZER_PADDING_START_PX = 16;
export const MESSAGE_VIRTUALIZER_OVERSCAN = 6;
export const MESSAGE_SMOOTH_SCROLL_MAX_VIEWPORTS = 2;

export function getScrollToBottomBehavior(
  scrollHeight: number,
  scrollTop: number,
  clientHeight: number,
): ScrollBehavior {
  const distanceFromBottom = Math.max(0, scrollHeight - scrollTop - clientHeight);
  const maxSmoothDistance = Math.max(0, clientHeight) * MESSAGE_SMOOTH_SCROLL_MAX_VIEWPORTS;
  return distanceFromBottom <= maxSmoothDistance ? "smooth" : "auto";
}

/**
 * Keep a message's virtual row identity stable when older history is prepended.
 * The index fallback only covers the transient out-of-range reads allowed by the
 * virtualizer while options are being reconciled.
 */
export function getMessageVirtualItemKey(
  messages: readonly MessageVirtualIdentity[],
  index: number,
): string | number {
  return messages[index]?.id ?? index;
}

/**
 * TanStack Virtual exposes a per-measurement override but does not provide a
 * "use the default" sentinel. Keep its current default for unrelated rows and
 * suppress only the message row whose disclosure was toggled by the user.
 */
export function shouldAdjustMessageScrollPositionOnItemSizeChange({
  activeMessageId,
  itemKey,
  itemStart,
  scrollOffset,
  scrollDirection,
  previouslyMeasured,
}: {
  activeMessageId: string | null;
  itemKey: string | number | bigint;
  itemStart: number;
  scrollOffset: number;
  scrollDirection: "forward" | "backward" | null;
  previouslyMeasured: boolean;
}): boolean {
  if (activeMessageId !== null && itemKey === activeMessageId) return false;
  return itemStart < scrollOffset
    && (!previouslyMeasured || scrollDirection !== "backward");
}
