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

export function parseCssPixels(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
