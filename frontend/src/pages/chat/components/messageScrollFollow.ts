export type MessageScrollSource = "automatic" | "user";
export type MessageScrollDirection = "away" | "toward" | "none";

export const MESSAGE_SCROLL_BOTTOM_EPSILON_PX = 1;

export function isMessageScrollAtBottom(
  scrollHeight: number,
  scrollTop: number,
  clientHeight: number,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= MESSAGE_SCROLL_BOTTOM_EPSILON_PX;
}

export function getMessageScrollDirection(
  previousScrollTop: number,
  nextScrollTop: number,
): MessageScrollDirection {
  if (nextScrollTop < previousScrollTop) return "away";
  if (nextScrollTop > previousScrollTop) return "toward";
  return "none";
}

export type MessageScrollFollowEvent =
  | { type: "follow-requested" }
  | { type: "detach-requested" }
  | {
    type: "viewport-scrolled";
    atBottom: boolean;
    source: MessageScrollSource;
    direction?: MessageScrollDirection;
  };

/**
 * “是否继续跟随最新输出”表达用户意图，不等同于某一帧是否物理贴底。
 * 虚拟列表测高和程序滚动都可能产生短暂的非贴底状态，不能因此退出跟随。
 */
export function reduceMessageScrollFollowing(
  following: boolean,
  event: MessageScrollFollowEvent,
): boolean {
  if (event.type === "follow-requested") return true;
  if (event.type === "detach-requested") return false;
  if (event.source === "automatic") return following;
  if (!event.atBottom) return false;
  if (event.direction === "away") return following;
  return true;
}

/**
 * 按钮只反映已经落地的视口位置，不响应尚未改变 scrollTop 的滚动意图。
 * 自动测高产生的瞬时位置变化也不应让按钮闪烁。
 */
export function reduceMessageScrollButtonVisible(
  visible: boolean,
  event: MessageScrollFollowEvent,
): boolean {
  if (event.type === "follow-requested") return false;
  if (event.type === "detach-requested" || event.source === "automatic") return visible;
  return !event.atBottom;
}

export function shouldFollowAfterTotalSizeChange(
  following: boolean,
  previousTotalSize: number | null,
  nextTotalSize: number,
): boolean {
  return following && previousTotalSize !== null && previousTotalSize !== nextTotalSize;
}
