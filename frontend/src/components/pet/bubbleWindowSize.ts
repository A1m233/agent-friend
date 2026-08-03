export interface BubbleBorderBoxSize {
  width: number;
  height: number;
}

export interface BubbleFramePadding {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface BubbleStackItemLayout {
  height: number;
  offset: number;
}

/** 与 Rust `bubble_window.rs::MAX_W` 对齐的独立透明窗宽度。 */
export const BUBBLE_WINDOW_WIDTH = 360;
/** Sonner toast 与原生窗口边界之间为阴影和关闭按钮预留的透明边距。 */
export const BUBBLE_FRAME_PADDING = 16;
/** 受 Rust `MAX_H=480` 约束；更多条目由 Sonner 留在队列中。 */
export const BUBBLE_VISIBLE_TOASTS = 4;

/**
 * The native bubble window must contain both the bubble border box and the
 * transparent frame padding reserved for its shadow.
 */
export function calculateBubbleWindowSize(
  bubble: BubbleBorderBoxSize,
  frame: BubbleFramePadding,
): BubbleBorderBoxSize {
  return {
    width: Math.ceil(bubble.width + frame.left + frame.right),
    height: Math.ceil(bubble.height + frame.top + frame.bottom),
  };
}

export function calculateBubbleStackWindowSize(
  items: readonly BubbleStackItemLayout[],
): BubbleBorderBoxSize {
  const stackHeight = items.reduce(
    (maximum, item) => Math.max(maximum, item.offset + item.height),
    0,
  );
  return {
    width: BUBBLE_WINDOW_WIDTH,
    height: Math.ceil(stackHeight + BUBBLE_FRAME_PADDING * 2),
  };
}

export function parseCssPixels(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
