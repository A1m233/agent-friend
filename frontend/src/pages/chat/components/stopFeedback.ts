import type { StopOutcome } from "@/stores/conversation";

// Acceptance is not a terminal outcome: never claim the answer has stopped here.
export const STOP_FEEDBACK: Record<StopOutcome, string | null> = {
  accepted: null,
  pending: "停止请求已保留，正在等待确认",
  "already-pending": "正在等待停止确认，请稍候",
  "no-session": "请先打开需要停止的对话",
  "no-active-run": "当前没有可停止的回复，回复可能已经结束",
  "not-controllable": "这项任务需要从发起它的入口停止",
  "state-changed": "任务状态尚未确认或已变化，请稍后重试",
  failed: "未能确认停止，请重试",
};
