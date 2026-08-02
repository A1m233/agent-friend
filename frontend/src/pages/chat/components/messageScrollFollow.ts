export type MessageScrollSource = "automatic" | "user";

export type MessageScrollFollowEvent =
  | { type: "follow-requested" }
  | { type: "viewport-scrolled"; atBottom: boolean; source: MessageScrollSource };

/**
 * “是否继续跟随最新输出”表达用户意图，不等同于某一帧是否物理贴底。
 * 虚拟列表测高和程序滚动都可能产生短暂的非贴底状态，不能因此退出跟随。
 */
export function reduceMessageScrollFollowing(
  following: boolean,
  event: MessageScrollFollowEvent,
): boolean {
  if (event.type === "follow-requested") return true;
  if (event.source === "automatic") return following;
  return event.atBottom;
}

export function shouldFollowAfterTotalSizeChange(
  following: boolean,
  previousTotalSize: number | null,
  nextTotalSize: number,
): boolean {
  return following && previousTotalSize !== null && previousTotalSize !== nextTotalSize;
}
